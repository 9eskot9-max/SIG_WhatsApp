frappe.ui.form.on('Purchase Order', {
    refresh(frm) {
        if (frm.doc.docstatus !== 1 || frm.__sig_whatsapp_button_added) return;
        frm.__sig_whatsapp_button_added = true;
        frm.add_custom_button(__('Send WhatsApp'), () => {
            frappe.confirm(__('Send this purchase order as a WhatsApp PDF?'), () => {
                frappe.call({
                    method: 'sig_whatsapp.maytapi.send_document',
                    args: { doctype: 'Purchase Order', name: frm.doc.name },
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
