frappe.ui.form.on('Sales Invoice', {
    refresh(frm) {
        if (frm.doc.docstatus !== 1 || frm.__sig_whatsapp_button_added) return;
        frm.__sig_whatsapp_button_added = true;
        frm.add_custom_button(__('Send WhatsApp'), () => {
            frappe.confirm(__('Send this invoice as a WhatsApp PDF?'), () => {
                frappe.call({
                    method: 'sig_whatsapp.maytapi.send_document',
                    args: { doctype: 'Sales Invoice', name: frm.doc.name },
                    freeze: true,
                    freeze_message: __('Generating and sending PDF...'),
                    callback: (r) => {
                        if (r.message) frappe.msgprint(__('Queued to {0}', [r.message.sent_to]));
                    }
                });
            });
        }, __('Actions'));
    }
});
