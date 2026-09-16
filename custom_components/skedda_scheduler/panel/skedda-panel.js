/**
 * Skedda Scheduler panel.
 *
 * Read-only on purpose: adding and editing go through Home Assistant's own
 * dialogs, so the forms, their validation and their translations have one
 * implementation rather than two.
 *
 * Plain custom element, no build step. It is served as-is from the
 * integration, so what ships is what was written.
 */

const STATUS_LABELS = {
  armed: "waiting for the window",
  disabled: "turned off",
  out_of_season: "out of season",
};

class SkeddaPanel extends HTMLElement {
  set hass(hass) {
    this._hass = hass;
    if (!this._loaded) {
      this._loaded = true;
      this._render();
      this._refresh();
      // The scheduler arms and fires without anything asking, so the view has
      // to look again on its own or it quietly goes stale.
      this._timer = setInterval(() => this._refresh(), 30000);
    }
  }

  disconnectedCallback() {
    clearInterval(this._timer);
  }

  async _refresh() {
    try {
      this._data = await this._hass.callWS({ type: "skedda_scheduler/overview" });
      this._error = null;
    } catch (err) {
      this._error = err.message || String(err);
    }
    this._paint();
  }

  _render() {
    this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; padding: 16px; }
        .card {
          background: var(--card-background-color, #fff);
          border-radius: var(--ha-card-border-radius, 12px);
          box-shadow: var(--ha-card-box-shadow, 0 2px 4px rgba(0,0,0,.1));
          margin-bottom: 16px;
          overflow: hidden;
        }
        h2 {
          font-size: 16px; font-weight: 500; margin: 0;
          padding: 16px 16px 8px; color: var(--primary-text-color);
        }
        table { width: 100%; border-collapse: collapse; }
        td, th {
          padding: 10px 16px; text-align: left; font-weight: 400;
          border-top: 1px solid var(--divider-color, #e0e0e0);
          color: var(--primary-text-color); font-size: 14px;
        }
        th { color: var(--secondary-text-color); font-size: 12px; border-top: none; }
        .muted { color: var(--secondary-text-color); }
        .dot { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 8px; }
        .ok { background: var(--success-color, #43a047); }
        .bad { background: var(--error-color, #db4437); }
        .empty { padding: 16px; color: var(--secondary-text-color); font-size: 14px; }
        .error { padding: 16px; color: var(--error-color, #db4437); font-size: 14px; }
        a { color: var(--primary-color); text-decoration: none; }
      </style>
      <div id="body"></div>
    `;
  }

  _paint() {
    const body = this.shadowRoot.getElementById("body");
    if (this._error) {
      body.innerHTML = `<div class="card"><div class="error">${escape(this._error)}</div></div>`;
      return;
    }
    if (!this._data) {
      body.innerHTML = `<div class="card"><div class="empty">Loading…</div></div>`;
      return;
    }
    const { accounts, bookings, jobs } = this._data;
    body.innerHTML = [
      section("Bookings", ["When", "Court", "Account", ""], bookings.map(bookingRow),
              "Nothing booked yet."),
      section("Booking jobs", ["Next slot", "Court", "Account", "Status"], jobs.map(jobRow),
              "No booking jobs. Add one from the integration page."),
      section("Accounts", ["Account", "Venue", "State", ""], accounts.map(accountRow),
              "No accounts."),
    ].join("");
  }
}

function section(title, headings, rows, empty) {
  const head = `<tr>${headings.map((h) => `<th>${escape(h)}</th>`).join("")}</tr>`;
  const inner = rows.length
    ? `<table>${head}${rows.join("")}</table>`
    : `<div class="empty">${escape(empty)}</div>`;
  return `<div class="card"><h2>${escape(title)}</h2>${inner}</div>`;
}

function bookingRow(booking) {
  return row([
    when(booking.start),
    escape(booking.court),
    escape(booking.account),
    `<span class="muted">${escape(booking.title || "")}</span>`,
  ]);
}

function jobRow(job) {
  const status = STATUS_LABELS[job.status] || job.status;
  const detail =
    job.status === "armed" && job.opens_at
      ? `${escape(status)} <span class="muted">— opens ${when(job.opens_at)}</span>`
      : escape(status);
  const repeat = job.repeat === "once" ? "" : ` <span class="muted">(${escape(job.repeat)})</span>`;
  return row([
    job.next_slot ? when(job.next_slot) : `<span class="muted">—</span>`,
    escape(job.court) + repeat,
    escape(job.account),
    detail,
  ]);
}

function accountRow(account) {
  const dot = account.authenticated ? "ok" : "bad";
  const state = account.authenticated ? "authorised" : "sign-in problem";
  return row([
    escape(account.title),
    escape(account.venue || ""),
    `<span class="dot ${dot}"></span>${escape(state)}`,
    `<a href="/config/integrations/integration/skedda_scheduler">Settings</a>`,
  ]);
}

function row(cells) {
  return `<tr>${cells.map((cell) => `<td>${cell}</td>`).join("")}</tr>`;
}

function when(iso) {
  const at = new Date(iso);
  return escape(
    at.toLocaleString(undefined, {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }),
  );
}

function escape(value) {
  return String(value).replace(/[&<>"']/g, (c) => {
    return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
  });
}

customElements.define("skedda-panel", SkeddaPanel);
