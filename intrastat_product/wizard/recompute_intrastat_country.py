# Copyright 2022 FactorLibre - Luis J. Salvatierra <luis.salvatierra@factorlibre.com>
from odoo import models, api


class AccountInvoiceRecomputeIntrastatCountry(models.TransientModel):
    _name = "account.invoice.recompute_intrastat_country"

    @api.multi
    def recompute_intrastat_country(self):
        invoice_ids = self._context.get('active_ids', [])
        invoices = self.env['account.invoice'].sudo().browse(invoice_ids)
        invoices._compute_intrastat_country()
