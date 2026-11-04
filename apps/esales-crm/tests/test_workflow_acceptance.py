"""Exercise private HTTP intake through stored PDF and local SMTP submission."""

import copy
from email import policy
from email.parser import BytesParser
from io import BytesIO
import json
import socket

from aiosmtpd.controller import Controller
from fastapi.testclient import TestClient
from pypdf import PdfReader
import pytest

from server.api import create_app
from server.config import Settings, hash_password


@pytest.mark.parametrize("purchase", ["renewal", "topup"])
def test_private_completed_purchase_reaches_smtp_once(tmp_path, event, purchase):
    class Inbox:
        def __init__(self):
            self.messages = []

        async def handle_DATA(self, server, session, envelope):
            self.messages.append((list(envelope.rcpt_tos), envelope.content))
            return "250 accepted"

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    inbox = Inbox()
    receiver = Controller(inbox, hostname="127.0.0.1", port=port)
    receiver.start()
    try:
        intake_key = "synthetic-acceptance-key-" + "x" * 32
        settings = Settings(
            data_dir=tmp_path,
            public_url="https://testserver",
            password_hash=hash_password("synthetic acceptance password"),
            intake_secret=intake_key,
            intake_environment="staging",
            smtp_host="127.0.0.1",
            smtp_port=port,
            smtp_tls=False,
            smtp_from="billing@example.test",
            delivery_enabled=True,
        )
        app = create_app(settings)
        store = app.state.store
        origin = {"Origin": settings.public_url}
        with TestClient(app, base_url=settings.public_url) as client:
            assert client.get("/api/merchants").status_code == 401
            assert client.post(
                "/api/login",
                json={"password": "synthetic acceptance password"},
                headers=origin,
            ).status_code == 200
            assert client.post(
                "/api/merchants",
                json={
                    "code": "SAMPLE",
                    "name": "TEST merchant",
                    "billing_name": "TEST billing company",
                    "email": "merchant@example.test",
                    "address": "Synthetic address, Macau",
                    "currency": "MOP",
                    "service_end": "2026-10-22",
                    "access_status": "grace",
                },
                headers=origin,
            ).status_code == 200
            assert client.put(
                "/api/settings",
                json={
                    "issuer": {
                        "name": "TEST ONLY Converto Digital",
                        "address": "Synthetic issuer address, Macau",
                        "email": "billing@example.test",
                    },
                    "prefix": "TEST-ACCEPTANCE-",
                    "issue_enabled": True,
                },
                headers=origin,
            ).status_code == 200

            payload = copy.deepcopy(event)
            payload["env"] = "staging"
            payload["data"]["code"] = "TEST-" + purchase
            # This is the payment label path used by the existing Make scenario.
            payload["data"]["payment"] = {"payment_method": {"name": "Bank transfer"}}
            if purchase == "topup":
                payload["data"].update(
                    items_type="etoken",
                    price=400,
                    payment_price=400,
                    coupon_discount=0,
                    coupon_code="",
                    items=[{
                        "system_product_type": "etoken",
                        "system_product_name": "易代幣 4000枚 TEST ONLY",
                        "quantity": 1,
                        "unit_price": 400,
                    }],
                )

            webhook = {"Authorization": "Bearer " + intake_key}
            response = client.post("/hooks/esales", json=payload, headers=webhook)
            assert response.status_code == 200
            assert response.json()["state"] == "accepted"
            assert client.post(
                "/hooks/esales", json=payload, headers=webhook,
            ).json()["duplicate"] is True
            row = client.get("/api/orders").json()[0]
            receipt = row["receipt_id"]
            snapshot = json.loads(store.one("SELECT snapshot FROM receipts")["snapshot"])
            assert snapshot["payment_method"] == "Bank transfer"
            expected_end = "2027-10-22" if purchase == "renewal" else "2026-10-22"
            merchant = client.get("/api/merchants").json()[0]
            assert merchant["service_end"] == expected_end
            if purchase == "topup":
                assert merchant["access_status"] == "grace"
            assert row["total_minor"] == (1540000 if purchase == "renewal" else 40000)

            preview = client.get(f"/api/receipts/{receipt}/pdf")
            assert preview.status_code == 200
            pdf_text = "".join(page.extract_text() for page in PdfReader(BytesIO(preview.content)).pages)
            assert "TEST-ACCEPTANCE-00001" in pdf_text
            assert "Bank transfer" in pdf_text
            assert ("15,400.00" if purchase == "renewal" else "400.00") in pdf_text
            assert ("系統月費" if purchase == "renewal" else "易代幣") in pdf_text

            outcome = client.post("/api/deliveries/process", headers=origin)
            assert outcome.status_code == 200
            assert outcome.json()["state"] == "submitted"
            assert len(inbox.messages) == 1
            recipients, raw_message = inbox.messages[0]
            assert recipients == ["merchant@example.test"]
            message = BytesParser(policy=policy.default).parsebytes(raw_message)
            assert message["Subject"] == "Payment receipt TEST-ACCEPTANCE-00001"
            attachments = list(message.iter_attachments())
            assert len(attachments) == 1
            assert attachments[0].get_payload(decode=True) == preview.content

            # Replaying the paid order after submission must not send again.
            assert client.post(
                "/hooks/esales", json=payload, headers=webhook,
            ).json()["duplicate"] is True
            assert client.post("/api/deliveries/process", headers=origin).json()["state"] == "idle"
            assert len(inbox.messages) == 1
            assert client.get("/api/orders").json()[0]["delivery_state"] == "submitted"
            assert store.one("SELECT count(*) n FROM receipts")["n"] == 1
    finally:
        receiver.stop()
