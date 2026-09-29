# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    ecommerce_order_identifier = fields.Char(
        string="E-commerce Order ID",
        readonly=True, copy=False)
    ec_order_ref = fields.Char(
        string="E-commerce Order Reference",
        readonly=True, copy=False)

    ecommerce_account_id = fields.Many2one(
        comodel_name='ecommerce.account',
        string="E-commerce Account", readonly=True, copy=False, ondelete='restrict')

    _unique_ecommerce_account_ecommerce_order_identifier = models.Constraint(
        'UNIQUE(ecommerce_account_id, ecommerce_order_identifier)',
        "E-commerce order identifier should be unique per ecommerce account.",
    )

    def _create_activity_resolve_fulfillment_conflict(self, user_id):
        """Create an activity on the E-commerce sale order for the salesperson to resolve
        the conflict of fulfillments received from E-commerce when order is fulfilled by Odoo.

        :param int user_id: The salesperson of the related E-commerce account.
        :return: None.
        """
        activity_message = self.env._(
            "This %s sale order is handled on Odoo, but pickings are also"
            " being created in %s. Please resolve this conflict.",
            self.ecommerce_account_id.ecommerce_channel_id.name, self.ecommerce_account_id.ecommerce_channel_id.name
        )
        self.activity_schedule(
            act_type_xmlid='mail.mail_activity_data_todo',
            user_id=user_id,
            note=activity_message,
        )

    def _action_cancel(self):
        if self.env.context.get('canceled_by_ecommerce'):
            orders_with_done_pickings = self.filtered(
                lambda o: o.picking_ids.filtered(lambda p: p.state == 'done')
            )
            for order in orders_with_done_pickings:
                order.activity_schedule(
                    act_type_xmlid='mail.mail_activity_data_todo',
                    user_id=order.user_id.id,
                    note=self.env._(
                        "This order has been cancelled by the %s merchant/customer but some "
                        "deliveries or returns are still associated with it. Please resolve "
                        "this conflict and adjust the stock to ensure proper synchronization.",
                        order.ecommerce_account_id.ecommerce_channel_id.name
                    ),
                )
        return super()._action_cancel()

    def _prepare_invoice(self):
        invoice_vals = super()._prepare_invoice()
        if self.ec_order_ref:
            invoice_vals.update({
                'invoice_date': self.env.context.get('invoice_date'),
                'payment_reference': self.ec_order_ref,
            })
        return invoice_vals
