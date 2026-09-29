from odoo import models, fields, api
from odoo.exceptions import UserError
from dateutil.relativedelta import relativedelta


class WarrantyRenewalWizard(models.TransientModel):
    _name = 'warranty.renewal.wizard'
    _description = 'Warranty Renewal Wizard'

    # Warranty reference and display fields
    warranty_id = fields.Many2one('product.warranty', string="Warranty", required=True)
    product_id = fields.Many2one('product.product', related='warranty_id.product_id',
                                 string="Product", readonly=True)
    serial_number = fields.Char(related='warranty_id.serial_number',
                                string="Serial No", readonly=True)
    partner_id = fields.Many2one('res.partner', related='warranty_id.partner_id',
                                 string="Partner", readonly=True)

    # Renewal details
    renewal_start_date = fields.Date(string="Renewal Start Date", default=fields.Date.context_today, required=True)
    renewal_amount = fields.Float(string="Renewal Amount", required=True)

    # Helper for configuration
    def _get_apply_on(self):
        return self.env['ir.config_parameter'].sudo().get_param(
            'is_product_warranty.apply_warranty_on', 'product'
        )

    # Fetch renewal settings
    def _get_renewal_settings_from_source(self, warranty):
        apply_on = self._get_apply_on()
        product = warranty.product_id

        if apply_on == 'product_variant':
            return {
                'allow_renewal': product.allow_renewal,
                'renewal_times': product.allow_warranty_renewal_times,
                'renewal_period': product.warranty_renewal_period,
                'renewal_cost': product.warranty_renewal_cost,
            }
        elif apply_on == 'product_category':
            cat = product.categ_id
            return {
                'allow_renewal': cat.allow_renewal,
                'renewal_times': cat.allow_warranty_renewal_times,
                'renewal_period': cat.warranty_renewal_period,
                'renewal_cost': cat.warranty_renewal_cost,
            }
        else:
            tmpl = product.product_tmpl_id
            return {
                'allow_renewal': tmpl.allow_renewal,
                'renewal_times': tmpl.allow_warranty_renewal_times,
                'renewal_period': tmpl.warranty_renewal_period,
                'renewal_cost': tmpl.warranty_renewal_cost,
            }

    # Set default values
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if self._context.get('active_id'):
            warranty = self.env['product.warranty'].browse(self._context.get('active_id'))
            res['warranty_id'] = warranty.id
            settings = self._get_renewal_settings_from_source(warranty)
            if settings['renewal_cost']:
                res['renewal_amount'] = settings['renewal_cost']
        return res

    # Perform renewal action
    def action_renew_warranty(self):
        warranty = self.warranty_id
        settings = self._get_renewal_settings_from_source(warranty)

        if not settings['allow_renewal']:
            raise UserError("Permission to renew the warranty for this product is not granted.")

        max_renewals = settings['renewal_times'] or 0
        if max_renewals and warranty.renewal_count >= max_renewals:
            raise UserError(
                "The maximum renewal limit (%s times) has been reached. "
                "This warranty can no longer be renewed." % max_renewals
            )

        renewal_months = settings['renewal_period'] or 12
        new_start_date = self.renewal_start_date or fields.Date.today()

        if (warranty.warranty_end_date and
                warranty.warranty_end_date < fields.Date.today() - relativedelta(months=1)):
            raise UserError(
                "The renewal period has expired! The end date for this warranty was %s."
                % warranty.warranty_end_date
            )

        new_end_date = new_start_date + relativedelta(months=renewal_months)

        if self.renewal_amount > 0:
            warranty_type = 'paid'
            paid_amt = self.renewal_amount
            free_amt = 0.0

            invoice = self.env['account.move'].create({
                'move_type': 'out_invoice',
                'partner_id': warranty.partner_id.id,
                'invoice_line_ids': [(0, 0, {
                    'name': f"Warranty Renewal: {warranty.receipt_no}",
                    'quantity': 1,
                    'price_unit': self.renewal_amount,
                })],
            })
            warranty.write({'invoice_id': invoice.id})
        else:
            warranty_type = 'free'
            paid_amt = 0.0
            free_amt = 0.0

        warranty.write({
            'state': 'draft',
            'warranty_start_date': new_start_date,
            'warranty_end_date': new_end_date,
            'warranty_period': renewal_months,
            'renewal_count': warranty.renewal_count + 1,
            'warranty_type': warranty_type,
        })

        self.env['warranty.history'].create({
            'warranty_id': warranty.id,
            'renewal_amount': self.renewal_amount,
            'date': fields.Date.today(),
            'warranty_start_date': new_start_date,
            'warranty_end_date': new_end_date,
            'paid_amount': paid_amt,
            'free_amount': free_amt,
        })

        return {'type': 'ir.actions.act_window_close'}
    