"""Real local SMTP failures for the two 1TICKS acceptance purchase types."""

import copy
import asyncio
from contextlib import contextmanager
from pathlib import Path
import socket

from aiosmtpd.controller import Controller
import pytest

from server.config import Settings
from server.delivery import process_delivery, retry_delivery
from server.orders import ingest


class LocalMailbox:
    def __init__(self, mode="accept"):
        self.mode = mode
        self.messages = []

    async def handle_RCPT(self, server, session, envelope, address, options):
        if self.mode == "reject_recipient":
            return "550 SIMULATED-DO-NOT-LEAK rejected recipient"
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        if self.mode == "reject_data":
            return "554 SIMULATED-DO-NOT-LEAK message rejected"
        self.messages.append(envelope.content)
        if self.mode == "delay_confirmation":
            await asyncio.sleep(1.5)
        if self.mode == "disconnect_after_data":
            server.transport.close()
            return "250 OK"
        return "250 accepted"

    async def handle_QUIT(self, server, session, envelope):
        if self.mode == "reject_quit":
            return "500 SIMULATED-DO-NOT-LEAK closing response"
        return "221 Bye"


@contextmanager
def mailbox(mode="accept"):
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    sink = LocalMailbox(mode)
    controller = Controller(sink, hostname="127.0.0.1", port=port)
    controller.start()
    try:
        yield sink, port
    finally:
        controller.stop()


def prepare_purchase(store, event, tmp_path, purchase):
    store.save_merchant({
        "code": "1ticks",
        "name": "1TICKS (TEST ONLY)",
        "billing_name": "1TICKS (TEST ONLY)",
        "email": "test-recipient@example.test",
        "address": "Synthetic test address, Macau",
        "currency": "MOP",
        "service_end": "2027-12-22",
        "access_status": "active",
    })
    payload = copy.deepcopy(event)
    payload["env"] = "staging"
    payload["data"].update(
        code="TEST-1TICKS-" + purchase,
        merchant_code="1ticks",
        merchant_subscription_end_date="2028-12-22",
        payment={"payment_method": {"name": "Bank transfer"}},
    )
    if purchase == "topup":
        payload["data"].update(
            items_type="etoken", price=400, payment_price=400,
            coupon_discount=0, coupon_code="", items=[{
                "system_product_type": "etoken",
                "system_product_name": "TEST ONLY 易代幣 4000枚",
                "quantity": 1, "unit_price": 400,
            }],
        )
    assert ingest(store, payload, environment="staging")["state"] == "accepted"
    settings = Settings(
        data_dir=tmp_path, intake_environment="staging",
        smtp_host="127.0.0.1", smtp_tls=False,
        smtp_from="billing@example.test", delivery_enabled=True,
    )
    return payload, settings


@pytest.mark.parametrize("purchase", ["renewal", "topup"])
@pytest.mark.parametrize("failure", ["connection", "authentication", "reject_recipient", "reject_data"])
def test_confirmed_failure_can_retry_same_receipt_without_reconciliation(store, event, tmp_path, purchase, failure):
    payload, settings = prepare_purchase(store, event, tmp_path, purchase)
    with mailbox(failure) as (sink, port), socket.socket() as closed_port:
        closed_port.bind(("127.0.0.1", 0))
        settings.smtp_port = closed_port.getsockname()[1] if failure == "connection" else port
        if failure == "authentication":
            # This local receiver deliberately does not advertise AUTH.
            settings.smtp_user = "synthetic-test-user"
            settings.smtp_password = "synthetic-test-password"
        result = process_delivery(store, settings)
        assert result["state"] == "failed"
        assert not sink.messages
        receipt = store.one("SELECT * FROM receipts")
        original_pdf = Path(receipt["pdf_path"]).read_bytes()
        original_snapshot = receipt["snapshot"]
        assert "SIMULATED-DO-NOT-LEAK" not in store.one("SELECT error FROM deliveries")["error"]

        sink.mode = "accept"
        settings.smtp_port = port
        settings.smtp_user = ""
        settings.smtp_password = ""
        retry_delivery(store, receipt["id"], "Corrected the confirmed local test failure")
        assert process_delivery(store, settings)["state"] == "submitted"
        assert len(sink.messages) == 1
        assert store.one("SELECT count(*) n FROM receipts")["n"] == 1
        current = store.one("SELECT * FROM receipts")
        assert current["snapshot"] == original_snapshot
        assert Path(current["pdf_path"]).read_bytes() == original_pdf
        assert ingest(store, payload, environment="staging")["duplicate"] is True
        assert process_delivery(store, settings)["state"] == "idle"


@pytest.mark.parametrize("purchase", ["renewal", "topup"])
def test_disconnect_after_data_keeps_receipt_uncertain_without_automatic_retry(store, event, tmp_path, purchase):
    _, settings = prepare_purchase(store, event, tmp_path, purchase)
    with mailbox("disconnect_after_data") as (sink, port):
        settings.smtp_port = port
        result = process_delivery(store, settings)
        assert result["state"] == "uncertain"
        assert len(sink.messages) == 1
        receipt = store.one("SELECT * FROM receipts")
        with pytest.raises(ValueError, match="Confirm the SMTP outcome"):
            retry_delivery(store, receipt["id"], "Try sending again")
        assert process_delivery(store, settings)["state"] == "idle"
        assert store.one("SELECT count(*) n FROM receipts")["n"] == 1
        assert store.one("SELECT attempts FROM deliveries")["attempts"] == 1


@pytest.mark.parametrize("purchase", ["renewal", "topup"])
def test_data_acceptance_survives_an_error_closing_the_smtp_session(store, event, tmp_path, purchase):
    _, settings = prepare_purchase(store, event, tmp_path, purchase)
    with mailbox("reject_quit") as (sink, port):
        settings.smtp_port = port
        result = process_delivery(store, settings)
        assert len(sink.messages) == 1
        assert result["state"] == "submitted"
        assert store.one("SELECT error FROM deliveries")["error"] is None
        assert process_delivery(store, settings)["state"] == "idle"


@pytest.mark.parametrize("purchase", ["renewal", "topup"])
@pytest.mark.parametrize("timeout,expected", [(1, "uncertain"), (5, "submitted")])
def test_configured_timeout_controls_wait_for_delayed_acceptance(store, event, tmp_path, purchase, timeout, expected):
    _, settings = prepare_purchase(store, event, tmp_path, purchase)
    settings.smtp_timeout_seconds = timeout
    with mailbox("delay_confirmation") as (sink, port):
        settings.smtp_port = port
        assert process_delivery(store, settings)["state"] == expected
        assert len(sink.messages) == 1
        assert store.one("SELECT count(*) n FROM receipts")["n"] == 1
        assert process_delivery(store, settings)["state"] == "idle"
