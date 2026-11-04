import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { decimalAmount, paymentTotals } from "./money";
import "./style.css";

type Merchant = {
  id: string;
  code: string;
  name: string;
  billing_name: string;
  email: string;
  phone: string;
  address: string;
  currency: string;
  plan: string;
  service_end: string | null;
  access_status: string;
  notes: string;
  updated_at: string;
  renewal_state: string;
  days_until_end: number | null;
};
type Order = {
  id: string;
  reference: string;
  merchant_code: string;
  kind: string;
  currency: string;
  total_minor: number;
  state: string;
  created_at: string;
  receipt_id: string | null;
  receipt_number: string | null;
  receipt_state: string | null;
  delivery_state: string | null;
  delivery_error: string | null;
  delivery_recipient: string | null;
  payload: string;
};
type Problem = {
  id: string;
  order_id: string;
  kind: string;
  detail: string;
  resolved_at: string | null;
  merchant_code: string;
  reference: string;
};
type Event = {
  id: string;
  action: string;
  subject: string;
  detail: string;
  created_at: string;
};
type Overview = {
  merchants: number;
  due_soon: number;
  overdue: number;
  unresolved: number;
  queued: number;
  orders: number;
  demo: boolean;
  delivery_enabled: boolean;
  reminders_enabled: boolean;
  last_event: { created_at: string } | null;
};
type Config = {
  issuer: { name: string; address: string; email: string };
  prefix: string;
  issue_enabled: boolean;
  sequence: number;
  smtp_configured: boolean;
  delivery_enabled: boolean;
};
async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const r = await fetch("/api/" + path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (r.status === 401 && path !== "login" && path !== "session") {
    window.dispatchEvent(new CustomEvent("crm-session-expired"));
    throw Error("Your session has expired. Please sign in again.");
  }
  if (!r.ok) {
    let d;
    try {
      d = await r.json();
    } catch {
      throw Error("Service unavailable. Please retry.");
    }
    throw Error(
      typeof d.detail === "string" ? d.detail : JSON.stringify(d.detail),
    );
  }
  return r.json();
}
const money = (n: number, c: string) =>
  c +
  " " +
  (n / 100).toLocaleString("en-GB", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
const day = (d: string | null) =>
  d
    ? new Date(d + "T12:00:00").toLocaleDateString("en-GB", {
        day: "numeric",
        month: "short",
        year: "numeric",
      })
    : "Not confirmed";
const stamp = (d: string) =>
  new Date(d).toLocaleString("en-GB", {
    timeZone: "Asia/Macau",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
const label = (s: string) =>
  ({
    due_soon: "Due soon",
    needs_review: "Needs review",
    submitted: "Submitted to email service",
    topup: "Token top-up",
    queued: "Queued",
    accepted: "Recorded",
    review: "Needs review",
    uncertain: "Check sending outcome",
  })[s] || s.replaceAll("_", " ").replace(/^./, (x) => x.toUpperCase());
function Badge({ state }: { state: string }) {
  return <span className={"badge " + state}>{label(state)}</span>;
}
function Empty({ title, detail }: { title: string; detail: string }) {
  return (
    <div className="empty">
      <span>○</span>
      <h3>{title}</h3>
      <p>{detail}</p>
    </div>
  );
}

function App() {
  const [session, setSession] = useState<{
      user: string;
      demo: boolean;
    } | null>(null),
    [checking, setChecking] = useState(true),
    [password, setPassword] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState("");
  const [view, setView] = useState("Overview"),
    [merchants, setMerchants] = useState<Merchant[]>([]),
    [orders, setOrders] = useState<Order[]>([]),
    [problems, setProblems] = useState<Problem[]>([]),
    [events, setEvents] = useState<Event[]>([]),
    [overview, setOverview] = useState<Overview | null>(null),
    [config, setConfig] = useState<Config | null>(null),
    [search, setSearch] = useState(""),
    [status, setStatus] = useState("all");
  const [selected, setSelected] = useState<Merchant | null>(null),
    [edit, setEdit] = useState<Merchant | Partial<Merchant> | null>(null),
    [purchase, setPurchase] = useState(false),
    [resend, setResend] = useState<{ id: string; recipient: string; state: string } | null>(
      null,
    ),
    [voiding, setVoiding] = useState<Order | null>(null),
    [busy, setBusy] = useState(false),
    [loaded, setLoaded] = useState(false);
  useEffect(() => {
    const expired = () => {
      setSession(null); setPassword(""); setLoaded(false);
      setMerchants([]); setOrders([]); setProblems([]); setEvents([]);
      setOverview(null); setConfig(null); setSelected(null); setEdit(null);
      setPurchase(false); setResend(null); setVoiding(null); setNotice("");
    };
    window.addEventListener("crm-session-expired", expired);
    return () => window.removeEventListener("crm-session-expired", expired);
  }, []);
  async function load() {
    try {
      const [m, o, p, e, v, c] = await Promise.all([
        api<Merchant[]>("merchants"),
        api<Order[]>("orders"),
        api<Problem[]>("exceptions"),
        api<Event[]>("events"),
        api<Overview>("overview"),
        api<Config>("settings"),
      ]);
      setMerchants(m);
      setOrders(o);
      setProblems(p);
      setEvents(e);
      setOverview(v);
      setConfig(c);
      setLoaded(true);
    } catch (e) {
      setError(String(e));
    }
  }
  useEffect(() => {
    api<{ user: string; demo: boolean }>("session")
      .then(setSession)
      .catch(() => {})
      .finally(() => setChecking(false));
  }, []);
  useEffect(() => {
    if (session) void load();
  }, [session]);
  async function action(fn: () => Promise<unknown>, message: string) {
    setBusy(true);
    setError("");
    try {
      await fn();
      setNotice(message);
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  function navigate(v: string) {
    setView(v);
    setSearch("");
    setStatus("all");
    setSelected(null);
  }
  const due = merchants
    .filter((m) => m.renewal_state !== "current")
    .sort((a, b) =>
      (a.service_end || "9999").localeCompare(b.service_end || "9999"),
    );
  const filtered = merchants.filter(
    (m) =>
      (status === "all" || m.renewal_state === status) &&
      (m.name + " " + m.code + " " + m.email)
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  const pending = problems.filter((p) => !p.resolved_at);
  if (checking) return <div className="loading">Opening your workspace…</div>;
  if (!session)
    return (
      <div className="login">
        <div className="login-brand">
          e-sales<span>OPERATIONS</span>
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void action(async () => {
              await api("login", "POST", { password });
              setSession(await api("session"));
              setPassword("");
            }, "Signed in");
          }}
        >
          <span className="eyebrow">PRIVATE WORKSPACE</span>
          <h1>
            A simpler way
            <br />
            to keep track.
          </h1>
          <p>Renewals, purchases and paid receipts in one place.</p>
          <label>
            Operator password
            <input
              autoFocus
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <button className="primary" disabled={busy}>
            Sign in →
          </button>
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
        </form>
      </div>
    );
  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="#" onClick={() => navigate("Overview")}>
          e-sales<span>RENEWALS & RECEIPTS</span>
        </a>
        <div className="workspace">
          <span className="workspace-icon">C</span>
          <div>
            Converto Digital<small>Merchant operations</small>
          </div>
        </div>
        <nav aria-label="Main navigation">
          {[
            "Overview",
            "Merchants",
            "Purchases",
            "Receipts",
            "Exceptions",
            "Activity",
            "Settings",
          ].map((v, i) => (
            <button
              key={v}
              className={view === v ? "active" : ""}
              onClick={() => navigate(v)}
            >
              <span className="nav-symbol">
                {["◫", "◎", "↗", "▤", "!", "◷", "⚙"][i]}
              </span>
              {v}
              {v === "Overview" && pending.length > 0 && (
                <b>{pending.length}</b>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <span className="status-dot" />{" "}
          {session.demo ? "Local demo workspace" : "Private operator workspace"}
          <small>Service dates in Macau time</small>
          <button
            onClick={() =>
              void action(async () => {
                await api("logout", "POST");
                setSession(null);
              }, "Signed out")
            }
          >
            Sign out
          </button>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <div>
            WORKSPACE <span>/</span> {view.toUpperCase()}
          </div>
          <div className="operator">
            <span>K</span>
            {session.user}
          </div>
        </header>
        {session.demo && (
          <div className="demo-banner">
            DEMO · Synthetic customers only. No live messages or e-sales
            changes.
          </div>
        )}
        <div className="page">
          <div className="page-heading">
            <div>
              <span className="eyebrow">
                {view === "Overview" ? "THE DAY AHEAD" : "YOUR WORKSPACE"}
              </span>
              <h1>
                {view === "Overview"
                  ? "Your renewals, in order."
                  : view === "Purchases"
                    ? "Every payment, accounted for."
                    : view === "Receipts"
                      ? "A clear record of payment."
                      : view}
              </h1>
              <p>
                {view === "Overview"
                  ? "Know what is due, what is paid and what needs a closer look."
                  : view === "Merchants"
                    ? "One record for each merchant, with the full renewal history."
                    : view === "Receipts"
                      ? "Issued documents stay unchanged. Delivery is tracked separately."
                      : view === "Purchases"
                        ? "Approved renewals and token purchases, linked to the original order."
                        : view === "Settings"
                          ? "Business details and the controls behind your receipts."
                          : "A record of changes, payments and delivery attempts."}
              </p>
            </div>
            <div className="heading-actions">
              <button onClick={() => void load()} disabled={busy}>
                ↻ Refresh
              </button>
              {view === "Merchants" ? (
                <button
                  className="primary"
                  onClick={() =>
                    setEdit({ currency: "MOP", access_status: "unknown" })
                  }
                >
                  + Add record
                </button>
              ) : (
                <button className="primary" onClick={() => setPurchase(true)}>
                  + Record payment
                </button>
              )}
            </div>
          </div>
          {error && (
            <div className="alert error" role="alert">
              {error}
              <button onClick={() => setError("")}>Dismiss</button>
            </div>
          )}
          {notice && (
            <div className="alert success" role="status">
              {notice}
              <button onClick={() => setNotice("")}>Dismiss</button>
            </div>
          )}
          {!loaded ? (
            <Empty
              title="Loading your workspace"
              detail="Retrieving customer records and receipt history."
            />
          ) : (
            <>
              {view === "Overview" && overview && (
                <>
                  <div className="metrics">
                    {[
                      [
                        "MERCHANT RECORDS",
                        overview.merchants,
                        "Existing customer base",
                      ],
                      [
                        "DUE WITHIN 30 DAYS",
                        overview.due_soon,
                        "Based on e-sales service end",
                      ],
                      [
                        "PAST SERVICE END",
                        overview.overdue,
                        "Payment review may be needed",
                      ],
                      [
                        "NEEDS ATTENTION",
                        overview.unresolved,
                        "Open review and delivery issues",
                      ],
                    ].map(([title, note, sub]) => (
                      <div className="metric" key={title}>
                        <span>{title}</span>
                        <strong>{note}</strong>
                        <small>{sub}</small>
                      </div>
                    ))}
                  </div>
                  <div className="dashboard-grid">
                    <section className="panel">
                      <div className="panel-heading">
                        <div>
                          <span className="eyebrow">RENEWAL WATCH</span>
                          <h2>Coming into focus</h2>
                        </div>
                        <button
                          className="text-button"
                          onClick={() => navigate("Merchants")}
                        >
                          All merchants ↗
                        </button>
                      </div>
                      {due.length ? (
                        <MerchantTable
                          rows={due.slice(0, 7)}
                          onSelect={setSelected}
                        />
                      ) : (
                        <Empty
                          title="No renewals need attention"
                          detail="Upcoming renewals will appear here."
                        />
                      )}
                      <div className="panel-footer">
                        Reminder deadline: e-sales service end date
                      </div>
                    </section>
                    <section className="panel attention">
                      <div className="panel-heading">
                        <div>
                          <span className="eyebrow">FOLLOW THROUGH</span>
                          <h2>A closer look</h2>
                        </div>
                        <span className="count">{pending.length}</span>
                      </div>
                      {pending.length ? (
                        pending.slice(0, 6).map((p) => (
                          <div className="problem" key={p.id}>
                            <span className="problem-dot" />
                            <div>
                              <strong>
                                {p.merchant_code || "Integration review"}
                              </strong>
                              <p>{p.detail}</p>
                              {p.order_id && p.kind === "review" && (
                                <button
                                  className="text-button"
                                  disabled={busy}
                                  onClick={() => {
                                    const reason = prompt(
                                      "Reason for releasing this order after review",
                                    );
                                    if (reason)
                                      void action(
                                        () =>
                                          api(
                                            "orders/" + p.order_id + "/release",
                                            "POST",
                                            { reason },
                                          ),
                                        "Order checked and released",
                                      );
                                  }}
                                >
                                  Review & release ↗
                                </button>
                              )}
                            </div>
                          </div>
                        ))
                      ) : (
                        <Empty
                          title="All clear"
                          detail="Unmatched payments and delivery issues will be visible here."
                        />
                      )}
                      <div className="panel-footer">
                        <button
                          className="text-button"
                          onClick={() => navigate("Exceptions")}
                        >
                          View every open issue ↗
                        </button>
                      </div>
                    </section>
                  </div>
                  <div className="integration-note">
                    <div>
                      <span className="status-dot" />
                      <strong>Payment flow</strong>
                      <p>已完成 → purple arrow → Make → CRM → receipt</p>
                    </div>
                    <div>
                      <strong>Last received order</strong>
                      <p>
                        {overview.last_event
                          ? stamp(overview.last_event.created_at)
                          : "No order received yet"}
                      </p>
                    </div>
                    <div>
                      <strong>Automation controls</strong>
                      <p>
                        {overview.reminders_enabled
                          ? "Make action policy enabled"
                          : "Reminder sending and access changes are off"}
                      </p>
                    </div>
                  </div>
                </>
              )}
              {view === "Merchants" && (
                <section className="panel">
                  <div className="table-tools">
                    <input
                      aria-label="Search merchants"
                      placeholder="Search merchant, code or email…"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                    />
                    <select
                      aria-label="Filter renewal state"
                      value={status}
                      onChange={(e) => setStatus(e.target.value)}
                    >
                      <option value="all">All records</option>
                      <option value="current">Current</option>
                      <option value="due_soon">Due soon</option>
                      <option value="overdue">Past service end</option>
                      <option value="needs_review">Needs review</option>
                    </select>
                    <span>{filtered.length} records</span>
                  </div>
                  {filtered.length ? (
                    <MerchantTable rows={filtered} onSelect={setSelected} />
                  ) : (
                    <Empty
                      title="No merchants found"
                      detail="Try another search or add an existing merchant record."
                    />
                  )}
                </section>
              )}
              {(view === "Purchases" || view === "Receipts") && (
                <section className="panel">
                  <div className="table-tools">
                    <input
                      aria-label="Search orders"
                      placeholder="Search order, merchant or receipt…"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                    />
                    <span>
                      {view === "Receipts"
                        ? "Paid receipts"
                        : "Purchase history"}
                    </span>
                    {view === "Receipts" && (
                      <button
                        disabled={busy}
                        title="Sending can take a minute. Wait for the status before retrying."
                        onClick={() =>
                          void action(
                            () => api("deliveries/process", "POST"),
                            "Delivery queue checked",
                          )
                        }
                      >
                        Process next queued receipt
                      </button>
                    )}
                  </div>
                  <OrderTable
                    rows={orders.filter(
                      (o) =>
                        (view !== "Receipts" || o.receipt_id) &&
                        (
                          o.reference +
                          " " +
                          o.merchant_code +
                          " " +
                          o.receipt_number
                        )
                          .toLowerCase()
                          .includes(search.toLowerCase()),
                    )}
                    receipts={view === "Receipts"}
                    onRetry={(id) => {
                      setResend({
                        id,
                        recipient:
                          orders.find((o) => o.receipt_id === id)
                            ?.delivery_recipient || "",
                        state: orders.find((o) => o.receipt_id === id)?.delivery_state || "",
                      });
                    }}
                    onVoid={(order) => {setError(""); setVoiding(order);}}
                  />
                  <div className="panel-footer">
                    {config?.delivery_enabled
                      ? "Email delivery enabled"
                      : "Email sending disabled until sender configuration is verified"}
                    . Submitted means accepted by the email service; it does not
                    prove delivery.
                  </div>
                  {view === "Receipts" && orders.some((o) => o.delivery_state === "blocked") && (
                    <p className="hint">Blocked receipts need a manual send after email is enabled. Review each receipt below before using Resend.</p>
                  )}
                </section>
              )}
              {view === "Activity" && (
                <section className="panel timeline">
                  {events.length ? (
                    events.map((e) => (
                      <article key={e.id}>
                        <time>{stamp(e.created_at)}</time>
                        <span className="timeline-dot" />
                        <div>
                          <strong>
                            {label(e.action.replaceAll(".", " "))}
                          </strong>
                          <p>{e.detail}</p>
                        </div>
                      </article>
                    ))
                  ) : (
                    <Empty
                      title="A fresh start"
                      detail="Actions will appear here as they happen."
                    />
                  )}
                </section>
              )}
              {view === "Exceptions" && (
                <section className="panel">
                  <div className="panel-heading">
                    <h2>Complete review queue</h2>
                    <span>{pending.length} open issues</span>
                  </div>
                  {pending.length ? (
                    pending.map((p) => (
                      <div className="problem" key={p.id}>
                        <span className="problem-dot" />
                        <div>
                          <strong>
                            {p.merchant_code || "Integration review"} ·{" "}
                            {label(p.kind)}
                          </strong>
                          <p>
                            {p.reference} {p.detail}
                          </p>
                          {p.kind === "review" && p.order_id ? (
                            <button
                              className="text-button"
                              disabled={busy}
                              onClick={() => {
                                const reason = prompt(
                                  "Confirm the payment and corrected merchant data; enter your review reason",
                                );
                                if (reason)
                                  void action(
                                    () =>
                                      api(
                                        "orders/" + p.order_id + "/release",
                                        "POST",
                                        { reason },
                                      ),
                                    "Held payment released",
                                  );
                              }}
                            >
                              Review & release
                            </button>
                          ) : (
                            <>
                              <button
                                className="text-button"
                                onClick={() =>
                                  navigate(
                                    p.kind === "delivery"
                                      ? "Receipts"
                                      : "Merchants",
                                  )
                                }
                              >
                                {p.kind === "delivery"
                                  ? "View receipt delivery"
                                  : "View merchant records"}{" "}
                                ↗
                              </button>
                              <button
                                style={{ marginLeft: 20 }}
                                className="text-button"
                                disabled={busy}
                                onClick={() => {
                                  const reason = prompt(
                                    "What did you verify or correct? This only closes the task; it does not change payments or e-sales access.",
                                  );
                                  if (reason)
                                    void action(
                                      () =>
                                        api(
                                          "exceptions/" + p.id + "/resolve",
                                          "POST",
                                          { reason },
                                        ),
                                      "Verification recorded",
                                    );
                                }}
                              >
                                Record resolution
                              </button>
                            </>
                          )}
                        </div>
                      </div>
                    ))
                  ) : (
                    <Empty
                      title="No open issues"
                      detail="Every outstanding review appears here, including older payments."
                    />
                  )}
                </section>
              )}
              {view === "Settings" && config && (
                <SettingsView
                  config={config}
                  busy={busy}
                  save={(c) =>
                    void action(
                      () => api("settings", "PUT", c),
                      "Receipt settings saved",
                    )
                  }
                />
              )}
            </>
          )}
          <footer className="page-footer">
            e-sales operations{" "}
            <span>Simple records. Clear follow-through.</span>
            <a href="/api/export" download="esales-export.json">
              Export records ↓
            </a>
          </footer>
        </div>
      </main>
      {selected && (
        <div className="overlay" onClick={() => setSelected(null)}>
          <aside
            className="drawer"
            role="dialog"
            aria-modal="true"
            aria-label="Merchant details"
            onClick={(e) => e.stopPropagation()}
          >
            <button className="close" onClick={() => setSelected(null)}>
              ×
            </button>
            <span className="eyebrow">MERCHANT RECORD</span>
            <h2>{selected.name}</h2>
            <p className="code">{selected.code}</p>
            <Badge state={selected.renewal_state} />
            <dl>
              {[
                ["Service end", day(selected.service_end)],
                ["Plan", selected.plan || "Not confirmed"],
                ["Billing name", selected.billing_name],
                ["Billing email", selected.email || "Missing"],
                ["Address", selected.address || "Not confirmed"],
                ["e-sales access", label(selected.access_status)],
                ["Notes", selected.notes || "No notes"],
              ].map(([a, b]) => (
                <div key={a}>
                  <dt>{a}</dt>
                  <dd>{b}</dd>
                </div>
              ))}
            </dl>
            <button
              className="primary"
              onClick={() => {
                setEdit(selected);
                setSelected(null);
              }}
            >
              Edit record
            </button>
            <h3>Purchase history</h3>
            {orders
              .filter(
                (o) =>
                  o.merchant_code.toLowerCase() === selected.code.toLowerCase(),
              )
              .map((o) => (
                <div className="history-card" key={o.id}>
                  <strong>{o.reference}</strong>
                  <p>
                    {label(o.kind)} · {money(o.total_minor, o.currency)}
                  </p>
                  {o.receipt_id && (
                    <a
                      href={"/api/receipts/" + o.receipt_id + "/pdf"}
                      target="_blank"
                      rel="noreferrer"
                    >
                      View receipt {o.receipt_number} ↗
                    </a>
                  )}
                </div>
              ))}
          </aside>
        </div>
      )}
      {edit && (
        <MerchantForm
          merchant={edit}
          busy={busy}
          close={() => setEdit(null)}
          save={async (data) => {
            await action(async () => {
              await api(
                edit.id ? "merchants/" + edit.id : "merchants",
                edit.id ? "PUT" : "POST",
                data,
              );
              setEdit(null);
            }, "Merchant record saved");
          }}
        />
      )}
      {purchase && (
        <PurchaseForm
          merchants={merchants}
          busy={busy}
          close={() => setPurchase(false)}
          submit={async (payload) => {
            await action(async () => {
              const result = await api<{ state: string }>(
                "orders",
                "POST",
                payload,
              );
              setPurchase(false);
              setView("Purchases");
              return result;
            }, "Payment recorded. Review its receipt or exception status below.");
          }}
        />
      )}
      {voiding && (
        <Modal title={"Void receipt " + voiding.receipt_number} close={() => setVoiding(null)}>
          <p>This marks the document void and keeps its history. It does not reverse payment or change e-sales service.</p>
          {voiding.kind === "renewal" && <p>After voiding, verify the actual e-sales service end and payment in Exceptions.</p>}
          <form onSubmit={(e) => {
            e.preventDefault();
            const reason = String(new FormData(e.currentTarget).get("reason") || "");
            void action(async () => {
              const result = await api<{service_reconciliation_required: boolean; delivery_reconciliation_required: boolean}>("receipts/" + voiding.receipt_id + "/void", "POST", {reason});
              setVoiding(null);
              if (result.service_reconciliation_required || result.delivery_reconciliation_required) navigate("Exceptions");
            }, "Receipt voided. Check any reconciliation issue in Exceptions.");
          }}>
            <label>Reason for voiding<input name="reason" required minLength={3} maxLength={1000} /></label>
            {error && <p className="error" role="alert">{error}</p>}
            <button className="primary" disabled={busy}>Confirm void receipt</button>
          </form>
        </Modal>
      )}
      {resend && (
        <Modal
          title="Resend the original receipt"
          close={() => setResend(null)}
        >
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const f = Object.fromEntries(new FormData(e.currentTarget));
              void action(async () => {
                await api("receipts/" + resend.id + "/retry", "POST", {
                  reason: ["submitted", "uncertain"].includes(resend.state) ? "RECONCILED: " + String(f.reason) : f.reason,
                  recipient: f.recipient,
                  allow_duplicate_copy: f.copy_ack === "on",
                });
                setResend(null);
              }, "Original receipt queued to the approved recipient");
            }}
          >
            <label>
              Explicitly approved recipient
              <input
                name="recipient"
                type="email"
                required
                defaultValue={resend.recipient}
              />
            </label>
            <label>
              Reason and verification
              <input name="reason" required minLength={3} maxLength={["submitted", "uncertain"].includes(resend.state) ? 988 : 1000} />
            </label>
            <p className="hint">
              The original receipt number and PDF stay unchanged. Record what
              you verified and why another copy is needed.
            </p>
            {["submitted", "uncertain", "delivered"].includes(resend.state) && (
              <label className="check"><input name="copy_ack" type="checkbox" required />I understand an earlier copy may have arrived and deliberately approve another copy.</label>
            )}
            {error && <p className="error" role="alert">{error}</p>}
            <button className="primary" disabled={busy}>
              Approve recipient & queue resend
            </button>
          </form>
        </Modal>
      )}
    </div>
  );
}

function MerchantTable({
  rows,
  onSelect,
}: {
  rows: Merchant[];
  onSelect: (m: Merchant) => void;
}) {
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>MERCHANT</th>
            <th>PLAN</th>
            <th>SERVICE END</th>
            <th>RENEWAL</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.id}>
              <td>
                <button className="merchant-link" onClick={() => onSelect(m)}>
                  {m.name}
                </button>
                <small className="code">{m.code}</small>
              </td>
              <td>{m.plan || "Unconfirmed"}</td>
              <td>
                {day(m.service_end)}
                <small>
                  {m.days_until_end === null
                    ? "Date needed"
                    : m.days_until_end < 0
                      ? Math.abs(m.days_until_end) + " days past end"
                      : m.days_until_end + " days remaining"}
                </small>
              </td>
              <td>
                <Badge state={m.renewal_state} />
              </td>
              <td>
                <button
                  aria-label={"Open " + m.name}
                  onClick={() => onSelect(m)}
                >
                  ↗
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function OrderTable({
  rows,
  receipts,
  onRetry,
  onVoid,
}: {
  rows: Order[];
  receipts: boolean;
  onRetry: (id: string) => void;
  onVoid: (order: Order) => void;
}) {
  return rows.length ? (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th>{receipts ? "RECEIPT / ORDER" : "ORDER"}</th>
            <th>MERCHANT</th>
            <th>TYPE</th>
            <th>PAID</th>
            <th>{receipts ? "DELIVERY" : "STATUS"}</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {rows.map((o) => (
            <tr key={o.id}>
              <td>
                <strong>{receipts ? o.receipt_number : o.reference}</strong>
                <small>{receipts ? o.reference : stamp(o.created_at)}</small>
              </td>
              <td>{o.merchant_code}</td>
              <td>{label(o.kind)}</td>
              <td className="amount">{money(o.total_minor, o.currency)}</td>
              <td>
                <Badge
                  state={
                    o.receipt_state === "void"
                      ? "void"
                      : receipts
                        ? o.delivery_state ||
                          (o.receipt_state === "legacy" ? "legacy" : "queued")
                        : o.state
                  }
                />
                {o.delivery_error && (
                  <small className="error">{o.delivery_error}</small>
                )}
                {receipts && o.delivery_recipient && (
                  <small>{o.delivery_recipient}</small>
                )}
              </td>
              <td>
                {o.receipt_id && (
                  <div className="row-actions">
                    <a
                      target="_blank"
                      rel="noreferrer"
                      href={"/api/receipts/" + o.receipt_id + "/pdf"}
                    >
                      PDF ↗
                    </a>
                    {receipts &&
                      o.receipt_state !== "void" &&
                      o.receipt_state !== "legacy" &&
                      o.delivery_state !== "processing" &&
                      o.delivery_state !== "queued" && (
                        <button onClick={() => onRetry(o.receipt_id!)}>
                          Resend
                        </button>
                      )}
                    {receipts && o.receipt_state !== "void" && o.receipt_state !== "legacy" &&
                      !["processing", "uncertain", "submitted"].includes(o.delivery_state || "") && (
                        <button onClick={() => onVoid(o)}>Void receipt</button>
                      )}
                  </div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <Empty
      title={receipts ? "No receipts issued yet" : "No purchases recorded yet"}
      detail="Completed e-sales orders will appear here after dispatch through Make."
    />
  );
}

function MerchantForm({
  merchant,
  busy,
  close,
  save,
}: {
  merchant: Partial<Merchant>;
  busy: boolean;
  close: () => void;
  save: (data: unknown) => Promise<void>;
}) {
  return (
    <Modal
      title={merchant.id ? "Edit merchant record" : "Add existing merchant"}
      close={close}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const f = new FormData(e.currentTarget);
          const data = Object.fromEntries([
            ...f.entries(),
            ["expected_updated_at", merchant.updated_at || null],
          ]);
          if (!data.service_end) data.service_end = null;
          void save(data);
        }}
      >
        <div className="form-grid">
          {[
            ["code", "Merchant code"],
            ["name", "Display name"],
            ["billing_name", "Billing name"],
            ["email", "Billing email"],
            ["phone", "Verified SMS number (+country code)"],
            ["plan", "Plan"],
          ].map(([key, title]) => (
            <label key={key}>
              {title}
              <input
                name={key}
                defaultValue={String(merchant[key as keyof Merchant] || "")}
                required={key === "code" || key === "name"}
                type={key === "email" ? "email" : "text"}
                readOnly={key === "code" && !!merchant.id}
              />
            </label>
          ))}
          <label>
            Currency
            <select name="currency" defaultValue={merchant.currency || "MOP"}>
              <option>MOP</option>
              <option>HKD</option>
            </select>
          </label>
          <label>
            e-sales service end
            <input
              name="service_end"
              type="date"
              defaultValue={merchant.service_end || ""}
            />
          </label>
          <label>
            Observed e-sales access
            <select
              name="access_status"
              defaultValue={merchant.access_status || "unknown"}
            >
              {["unknown", "active", "grace", "inactive"].map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
          </label>
        </div>
        <label>
          Billing address
          <textarea name="address" defaultValue={merchant.address || ""} />
        </label>
        <label>
          Notes
          <textarea name="notes" defaultValue={merchant.notes || ""} />
        </label>
        <label>
          Reason for this record or correction
          <input
            name="reason"
            required
            minLength={3}
            placeholder="Source or reason for change"
          />
        </label>
        <p className="hint">
          Editing this record does not change e-sales access or issued receipts.
        </p>
        <button className="primary" disabled={busy}>
          Save record
        </button>
      </form>
    </Modal>
  );
}
function Modal({
  title,
  close,
  children,
}: {
  title: string;
  close: () => void;
  children: React.ReactNode;
}) {
  useEffect(() => {
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") close();
    };
    document.addEventListener("keydown", esc);
    return () => document.removeEventListener("keydown", esc);
  }, [close]);
  return (
    <div className="overlay">
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <button className="close" onClick={close} aria-label="Close">
          ×
        </button>
        <span className="eyebrow">E-SALES OPERATIONS</span>
        <h2>{title}</h2>
        {children}
      </section>
    </div>
  );
}
function PurchaseForm({
  merchants,
  busy,
  close,
  submit,
}: {
  merchants: Merchant[];
  busy: boolean;
  close: () => void;
  submit: (data: unknown) => Promise<void>;
}) {
  const [kind, setKind] = useState("monthly");
  const [quantity, setQuantity] = useState("12"),
    [unitPrice, setUnitPrice] = useState(""),
    [discount, setDiscount] = useState("0"),
    [merchantCode, setMerchantCode] = useState(""),
    [validation, setValidation] = useState("");
  let totals: ReturnType<typeof paymentTotals> | null = null;
  try { totals = paymentTotals(unitPrice, quantity, discount); } catch {}
  const merchant = merchants.find((m) => m.code === merchantCode);
  return (
    <Modal title="Record an approved payment" close={close}>
      <p>
        Use the original e-sales order reference. Payment proof must already
        have been reviewed.
      </p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const f = Object.fromEntries(new FormData(e.currentTarget));
          const m = merchants.find((m) => m.code === f.merchant_code)!;
          if (!m) { setValidation("Choose a merchant before recording payment."); return; }
          let amounts;
          try { amounts = paymentTotals(unitPrice, quantity, discount); }
          catch (error) { setValidation(String(error)); return; }
          setValidation("");
          void submit({
            payment_confirmed: f.confirmed === "on",
            reason: f.reason,
            payload: {
              env: "production",
              data: {
                code: f.reference,
                merchant_code: f.merchant_code,
                currency: m.currency,
                status: "completed",
                price: decimalAmount(amounts.subtotal),
                payment_price: decimalAmount(amounts.paid),
                coupon_discount: decimalAmount(amounts.discount),
                coupon_code: f.coupon,
                merchant_subscription_end_date: f.service_end || null,
                payment: { name: f.payment_method },
                items: [
                  {
                    system_product_type: kind,
                    system_product_name: f.description,
                    quantity: Number(quantity),
                    unit_price: decimalAmount(amounts.unit),
                  },
                ],
              },
            },
          });
        }}
      >
        <div className="form-grid">
          <label>
            Merchant
            <select name="merchant_code" required value={merchantCode} onChange={(e) => setMerchantCode(e.target.value)}>
              <option value="">Choose merchant</option>
              {merchants.map((m) => (
                <option key={m.id} value={m.code}>
                  {m.name} ({m.currency})
                </option>
              ))}
            </select>
          </label>
          <label>
            Original order reference
            <input name="reference" required maxLength={120} />
          </label>
          <label>
            Purchase type
            <select value={kind} onChange={(e) => {
              setKind(e.target.value); setQuantity(e.target.value === "monthly" ? "12" : "1");
              setUnitPrice(""); setDiscount("0"); setValidation("");
            }}>
              <option value="monthly">Subscription renewal</option>
              <option value="etoken">SMS / e-token top-up</option>
            </select>
          </label>
          <label>
            Quantity
            <input
              name="quantity"
              type="number"
              min="1"
              max="10000"
              step="1"
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
              required
            />
          </label>
          <label>
            Unit price
            <input
              name="unit_price"
              type="number"
              min="0"
              step="0.01"
              value={unitPrice}
              onChange={(e) => setUnitPrice(e.target.value)}
              required
            />
          </label>
          <label>
            Discount amount
            <input
              name="discount"
              type="number"
              min="0"
              step="0.01"
              value={discount}
              onChange={(e) => setDiscount(e.target.value)}
            />
          </label>
          <label>
            Discount code
            <input name="coupon" />
          </label>
          <label>
            Payment method
            <input
              name="payment_method"
              defaultValue="Bank transfer"
              required
            />
          </label>
          {kind === "monthly" && (
            <label>
              e-sales service end after renewal
              <input name="service_end" type="date" required />
            </label>
          )}
        </div>
        <label>
          Purchase description
          <textarea name="description" required maxLength={2000} />
        </label>
        <label>
          Approval note
          <input
            name="reason"
            required
            minLength={3}
            placeholder="What payment evidence did you review?"
          />
        </label>
        <p className="hint" role="status">
          {totals && merchant ? <>
            Subtotal: {money(totals.subtotal, merchant.currency)}.
            {" "}Discount: {money(totals.discount, merchant.currency)}.
            {" "}<strong>Amount paid: {money(totals.paid, merchant.currency)}</strong>
          </> : "Choose a merchant and enter valid amounts to check the payment total."}
        </p>
        {validation && <p className="error" role="alert">{validation}</p>}
        <label className="check">
          <input type="checkbox" name="confirmed" required /> I reviewed the
          payment proof and confirmed payment.
        </label>
        <p className="hint">
          This records the purchase and issues one receipt. It does not apply
          tokens or extend service again in e-sales.
        </p>
        <button className="primary" disabled={busy}>
          Record payment & create receipt
        </button>
      </form>
    </Modal>
  );
}
function SettingsView({
  config,
  busy,
  save,
}: {
  config: Config;
  busy: boolean;
  save: (c: unknown) => void;
}) {
  return (
    <div className="settings-grid">
      <section className="panel settings-panel">
        <span className="eyebrow">RECEIPT IDENTITY</span>
        <h2>Your issuing business</h2>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const f = Object.fromEntries(new FormData(e.currentTarget));
            save({
              issuer: { name: f.name, address: f.address, email: f.email },
              prefix: f.prefix,
              issue_enabled: f.approved === "on",
            });
          }}
        >
          <label>
            Business name
            <input name="name" defaultValue={config.issuer.name} required />
          </label>
          <label>
            Business address
            <textarea
              name="address"
              defaultValue={config.issuer.address}
              required
            />
          </label>
          <label>
            Contact email
            <input
              name="email"
              type="email"
              defaultValue={config.issuer.email}
              required
            />
          </label>
          <label>
            Receipt series
            <input
              name="prefix"
              defaultValue={config.prefix}
              pattern="[A-Z0-9-]{2,30}"
              readOnly={config.sequence > 0}
            />
          </label>
          <p className="hint">
            Use a distinct series. It is locked after the first issue to
            preserve numbering.
          </p>
          <label className="check">
            <input
              type="checkbox"
              name="approved"
              defaultChecked={config.issue_enabled}
            />{" "}
            I verified the issuer details, numbering and zero-tax receipt
            treatment.
          </label>
          <button className="primary" disabled={busy}>
            Save receipt settings
          </button>
        </form>
      </section>
      <div>
        <section className="panel settings-panel">
          <span className="eyebrow">DELIVERY & AUTOMATION</span>
          <h2>Connection status</h2>
          <dl>
            <div>
              <dt>Email sender</dt>
              <dd>
                <Badge state={config.smtp_configured ? "current" : "blocked"} />
              </dd>
            </div>
            <div>
              <dt>Live sending</dt>
              <dd>{config.delivery_enabled ? "Enabled" : "Disabled"}</dd>
            </div>
            <div>
              <dt>Renewal deadline</dt>
              <dd>e-sales service end</dd>
            </div>
          </dl>
          <p className="hint">
            Credentials stay on the server. Make intake and email setup are
            documented in the local handoff.
          </p>
        </section>
        <AutomationSettings />
        <section className="note-card">
          <h3>Your existing approval stays.</h3>
          <p>
            Review the transfer proof in e-sales, set 訂單狀態 to 已完成, then
            click the purple arrow. Make can deliver the completed order here.
          </p>
        </section>
      </div>
    </div>
  );
}

type Policy = {
  enabled: boolean;
  approved: boolean;
  lead_days: number;
  grace_days: number;
  lead_template: string;
  due_template: string;
};
type PolicyAction = {id: string; merchant_code: string; service_end: string; kind: string; state: string};
function AutomationSettings() {
  const [p, setP] = useState<Policy | null>(null),
    [error, setError] = useState(""),
    [message, setMessage] = useState(""),
    [actions, setActions] = useState<PolicyAction[]>([]),
    [retry, setRetry] = useState<PolicyAction | null>(null),
    [retryBusy, setRetryBusy] = useState(false);
  const refreshActions = () => api<PolicyAction[]>("actions").then(setActions).catch((e) => setError(String(e)));
  useEffect(() => {
    api<Policy>("policy")
      .then(setP)
      .catch((e) => setError(String(e)));
    void refreshActions();
  }, []);
  return (
    <section className="panel settings-panel" style={{ marginTop: 24 }}>
      <span className="eyebrow">MAKE ACTION POLICY</span>
      <h2>Renewal follow-through</h2>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {message && <p role="status">{message}</p>}
      {p && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            const f = Object.fromEntries(new FormData(e.currentTarget));
            api<Policy>("policy", "PUT", {
              enabled: f.enabled === "on",
              approved: f.approved === "on",
              lead_days: Number(f.lead_days),
              grace_days: Number(f.grace_days),
              lead_template: f.lead_template,
              due_template: f.due_template,
            })
              .then((v) => {
                setP(v);
                setError("");
                setMessage(
                  "Policy saved. Make must be connected to process actions.",
                );
                void refreshActions();
              })
              .catch((e) => setError(String(e)));
          }}
        >
          <div className="form-grid">
            <label>
              Reminder days before service end
              <input
                name="lead_days"
                type="number"
                min="1"
                max="30"
                defaultValue={p.lead_days}
              />
            </label>
            <label>
              Days after service end before suspension
              <input
                name="grace_days"
                type="number"
                min="1"
                max="30"
                defaultValue={p.grace_days}
              />
            </label>
          </div>
          <label>
            Reminder SMS
            <textarea name="lead_template" defaultValue={p.lead_template} />
          </label>
          <label>
            Expiry SMS
            <textarea name="due_template" defaultValue={p.due_template} />
          </label>
          <p className="hint">
            Use {"{merchant}"} and {"{service_end}"} as placeholders. Make sends
            SMS and changes e-sales status. The two-day reminder and three-day
            suspension values are proposed defaults, not activated rules.
          </p>
          <label className="check">
            <input
              name="approved"
              type="checkbox"
              defaultChecked={p.approved}
            />{" "}
            I verified SMS recipients, message text, grace timing and the
            e-sales access behavior.
          </label>
          <label className="check">
            <input name="enabled" type="checkbox" defaultChecked={p.enabled} />{" "}
            Enable the action feed for the configured Make scenario.
          </label>
          <button>Save action policy</button>
        </form>
      )}
      {actions.length > 0 && <>
        <h3>Reminder and access actions</h3>
        <button type="button" onClick={() => void refreshActions()}>Refresh actions</button>
        <div className="table-scroll"><table><thead><tr><th>MERCHANT</th><th>ACTION</th><th>STATE</th><th /></tr></thead><tbody>
          {actions.map((item) => <tr key={item.id}>
            <td>{item.merchant_code}<small>{day(item.service_end)}</small></td>
            <td>{label(item.kind)}</td><td><Badge state={item.state} /></td>
            <td>{p?.enabled && ["failed", "uncertain", "cancelled"].includes(item.state) && <button onClick={() => {setError(""); setRetry(item);}}>Review retry</button>}</td>
          </tr>)}
        </tbody></table></div>
      </>}
      {retry && <Modal title="Review action retry" close={() => setRetry(null)}>
        <p>Check the Make execution and actual merchant/provider result before retrying {retry.kind} for {retry.merchant_code}.</p>
        <form onSubmit={(e) => {
          e.preventDefault();
          const note = String(new FormData(e.currentTarget).get("reason") || "");
          const reason = retry.state === "uncertain" ? "RECONCILED: " + note : note;
          setRetryBusy(true);
          void api("actions/" + retry.id + "/retry", "POST", {reason})
            .then(async () => {setRetry(null); setError(""); setMessage("Action queued after review. Make will process it."); await refreshActions();})
            .catch((e) => setError(String(e)))
            .finally(() => setRetryBusy(false));
        }}>
          <label>Verified outcome and reason<input name="reason" required minLength={3} maxLength={retry.state === "uncertain" ? 988 : 1000} /></label>
          {retry.state === "uncertain" && <label className="check"><input type="checkbox" required />I verified that the earlier attempt did not apply the SMS or access change.</label>}
          {error && <p className="error" role="alert">{error}</p>}
          <button className="primary" disabled={retryBusy}>Queue reviewed retry</button>
        </form>
      </Modal>}
    </section>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
