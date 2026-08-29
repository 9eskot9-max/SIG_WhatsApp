"""Secure Maytapi document delivery for ERPNext.

Credentials are read from the single ``Maytapi Settings`` DocType and are never
written to source control or application logs.  Sending is explicit (a button
or API call), and test mode redirects every message to the configured test
number while retaining the resolved recipient in the audit log.
"""

from __future__ import annotations

import base64
import json
import re
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import frappe
from frappe import _
from frappe.utils.pdf import get_pdf


MAX_PDF_BYTES = 10 * 1024 * 1024
DEFAULT_ENDPOINT = "https://api.maytapi.com/api"
PHONE_RE = re.compile(r"^\d{8,15}$")
GROUP_RE = re.compile(r"^[0-9-]+@g\.us$")


def _settings():
    if not frappe.db.exists("DocType", "Maytapi Settings"):
        frappe.throw(_("Maytapi Settings is not installed."))
    settings = frappe.get_single("Maytapi Settings")
    if not settings.enabled:
        frappe.throw(_("Maytapi integration is disabled."))
    for field in ("product_id", "phone_id", "api_token"):
        if not settings.get(field):
            frappe.throw(_("Maytapi setting {0} is required.").format(field))
    return settings


def _safe_segment(value: str, label: str) -> str:
    value = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        frappe.throw(_("Invalid Maytapi {0}.").format(label))
    return value


def normalize_recipient(value: str) -> str:
    """Normalize an individual number or preserve a Maytapi group id."""
    value = str(value or "").strip()
    if GROUP_RE.fullmatch(value):
        return value
    digits = re.sub(r"[^0-9]", "", value)
    if not PHONE_RE.fullmatch(digits):
        frappe.throw(_("Enter a valid WhatsApp number or group ID."))
    return digits


def _mask(value: str) -> str:
    if "@g.us" in value:
        return value[:4] + "...@g.us"
    return "***" + value[-4:]


def _resolve_contact_phone(doctype: str, name: str) -> str:
    doc = frappe.get_doc(doctype, name)
    party_field = "customer" if doctype == "Sales Invoice" else "supplier"
    party = doc.get(party_field)
    if not party:
        frappe.throw(_("{0} has no {1}.").format(doctype, party_field))
    rows = frappe.db.sql(
        """
        SELECT c.mobile_no, c.phone
        FROM `tabContact` c
        INNER JOIN `tabDynamic Link` dl ON dl.parent = c.name
        WHERE dl.link_doctype = %(party_doctype)s
          AND dl.link_name = %(party)s
          AND c.docstatus < 2
        ORDER BY c.is_primary_contact DESC, c.modified DESC
        LIMIT 1
        """,
        {"party_doctype": party_field.title(), "party": party},
        as_dict=True,
    )
    if not rows:
        frappe.throw(_("No contact phone found for {0} {1}.").format(party_field, party))
    return normalize_recipient(rows[0].get("mobile_no") or rows[0].get("phone"))


def _audit(doctype: str, name: str, resolved: str, sent_to: str, status: str, message_id: str = "", error: str = ""):
    if not frappe.db.exists("DocType", "Maytapi Message Log"):
        frappe.logger("maytapi").info(
            "document=%s/%s resolved=%s sent_to=%s status=%s message_id=%s error=%s",
            doctype, name, _mask(resolved), _mask(sent_to) if sent_to else "", status, message_id, error,
        )
        return
    frappe.get_doc({
        "doctype": "Maytapi Message Log",
        "reference_doctype": doctype,
        "reference_name": name,
        "resolved_recipient": _mask(resolved),
        "sent_recipient": _mask(sent_to) if sent_to else "",
        "status": status,
        "message_id": message_id or "",
        "error": (error or "")[:500],
    }).insert(ignore_permissions=True)


def _post(settings, payload: dict) -> dict:
    base = (settings.endpoint or DEFAULT_ENDPOINT).rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" or parsed.hostname not in {"api.maytapi.com", "maytapi.com"}:
        frappe.throw(_("Maytapi endpoint must use HTTPS and a maytapi.com host."))
    product = _safe_segment(settings.product_id, "product ID")
    phone = _safe_segment(settings.phone_id, "phone ID")
    url = f"{base}/{product}/{phone}/sendMessage"
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-maytapi-key": settings.get_password("api_token")},
        method="POST",
    )
    try:
        with urlopen(request, timeout=max(5, min(int(settings.timeout_seconds or 30), 120))) as response:
            body = response.read(256 * 1024).decode("utf-8", errors="replace")
            return {"status": response.status, "body": body}
    except HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace")
        raise frappe.ValidationError(_("Maytapi HTTP {0}: {1}").format(exc.code, body[:500])) from exc
    except URLError as exc:
        raise frappe.ValidationError(_("Maytapi connection failed: {0}").format(str(exc.reason)[:300])) from exc


@frappe.whitelist()
def send_document(doctype: str, name: str, to: str | None = None, caption: str | None = None, print_format: str | None = None):
    """Queue/send a submitted Sales Invoice or Purchase Order PDF."""
    if doctype not in {"Sales Invoice", "Purchase Order"}:
        frappe.throw(_("Only Sales Invoice and Purchase Order documents are supported."))
    doc = frappe.get_doc(doctype, name)
    if not frappe.has_permission(doctype, "read", doc):
        frappe.throw(_("You do not have permission to read this document."), frappe.PermissionError)
    if doc.docstatus != 1:
        frappe.throw(_("Only submitted documents can be sent."))

    settings = _settings()
    # In testing mode the configured test number is sufficient, which allows
    # validation before customer/supplier contacts have been migrated.
    resolved = normalize_recipient(to) if to else (
        normalize_recipient(settings.test_phone_number)
        if settings.test_mode
        else _resolve_contact_phone(doctype, name)
    )
    sent_to = normalize_recipient(settings.test_phone_number) if settings.test_mode else resolved
    selected_format = print_format or settings.get("sales_invoice_print_format" if doctype == "Sales Invoice" else "purchase_order_print_format")
    html = frappe.get_print(doctype, name, print_format=selected_format or None, doc=doc)
    pdf = get_pdf(html)
    if not pdf or len(pdf) > MAX_PDF_BYTES:
        frappe.throw(_("Generated PDF is empty or exceeds 10 MB."))
    filename = f"{name}.pdf"
    payload = {
        "to_number": sent_to,
        "type": "media",
        "message": "data:application/pdf;base64," + base64.b64encode(pdf).decode("ascii"),
        "filename": filename,
        "text": caption or f"{doctype}: {name}",
    }
    try:
        result = _post(settings, payload)
        if not 200 <= int(result.get("status", 0)) < 300:
            raise frappe.ValidationError(_("Maytapi returned HTTP {0}.").format(result.get("status")))
        try:
            response = json.loads(result.get("body") or "{}")
        except json.JSONDecodeError:
            response = {}
        message_id = str(response.get("message_id") or response.get("id") or response.get("data", {}).get("id", ""))
        _audit(doctype, name, resolved, sent_to, "QUEUED", message_id)
        return {"status": "QUEUED", "message_id": message_id, "sent_to": _mask(sent_to), "testing": bool(settings.test_mode)}
    except Exception as exc:
        safe_error = str(exc)[:500]
        _audit(doctype, name, resolved, sent_to, "FAILED", error=safe_error)
        raise
