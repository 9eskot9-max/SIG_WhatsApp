app_name = "sig_whatsapp"
app_title = "SIG WhatsApp"
app_publisher = "SIG"
app_description = "Secure Maytapi WhatsApp document delivery for ERPNext"
app_email = "it@sigtele.com"
app_license = "mit"

doctype_js = {
    "Sales Invoice": "public/js/sales_invoice.js",
    "Purchase Order": "public/js/purchase_order.js",
}

# SIG brand look (MASTER-ERP-INTEGRATION.md, Workspace and interface design).
# Logos are swapped via CSS content: url() inside sig_theme.css — frappe's own
# app_logo_url default sorts first in the hook chain and cannot be overridden.
app_include_css = "/assets/sig_whatsapp/css/sig_theme.css"
