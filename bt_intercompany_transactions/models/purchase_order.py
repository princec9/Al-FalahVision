from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools import float_is_zero


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    is_intercompany_bill = fields.Boolean(
        string="Is Intercompany Bill",
         tracking=True
    )
    
    is_msupply = fields.Boolean(
        string="MSupply"
    )

    company_to_bill_id = fields.Many2one(
        "res.company",
        string="Company To Bill",
        check_company=False,
         tracking=True
    )

    @api.constrains("company_to_bill_id")
    def _check_company_to_bill(self):
        for order in self:
            if (
                order.is_intercompany_bill
                and order.company_to_bill_id == order.company_id
            ):
                raise ValidationError(
                    _("Company To Bill must be different from current company.")
                )

    def action_generate_intercompany_bill(self):

        AccountMove = self.env["account.move"]
        AccountTax = self.env["account.tax"]

        for order in self:

            if not order.is_intercompany_bill:
                continue

            if order.invoice_status == "invoiced":
                raise ValidationError(
                    _("All products are already billed.")
                )

            if not order.company_to_bill_id:
                raise ValidationError(
                    _("Please select Company To Bill.")
                )

            company_to_bill = order.company_to_bill_id

            invoice_lines = []

            for line in order.order_line.filtered(
                lambda l: not l.display_type and l.qty_to_invoice > 0
            ):

                taxes = AccountTax.sudo().with_company(
                    company_to_bill
                ).search([
                    ("company_id", "=", company_to_bill.id),
                    ("type_tax_use", "=", "purchase"),
                    ("name", "in", line.tax_ids.mapped("name")),
                ])

                product_in_target_company = line.product_id.with_company(
                    company_to_bill
                )
                
                invoice_line_vals = {
                    "name": line.name,
                    "quantity": line.qty_to_invoice,
                    "price_unit": line.price_unit,
                    "intercompany_purchase_line_id": line.id,
                }

                if product_in_target_company.exists():
                    product_category_in_target_company = product_in_target_company.categ_id.with_company(company_to_bill)
                    target_account = (
                        product_in_target_company.property_account_expense_id or
                        product_category_in_target_company.property_account_expense_categ_id or
                        company_to_bill.expense_account_id
                    )
                    invoice_line_vals["product_id"] = product_in_target_company.id

                    if target_account:
                        invoice_line_vals["account_id"] = target_account.id

                invoice_lines.append((0, 0, invoice_line_vals))

            if not invoice_lines:
                raise ValidationError(
                    _("No billable lines found.")
                )

            bill = AccountMove.sudo().with_company(
                company_to_bill
            ).create({
                "move_type": "in_invoice",
                "company_id": company_to_bill.id,
                "partner_id": order.partner_id.id,
                "currency_id": order.currency_id.id,
                "invoice_line_ids": invoice_lines,
            })

            bill.message_post(
                body=Markup(
                    "Vendor Bill created from Purchase Order "
                    "<b>%s</b> of company <b>%s</b>."
                ) % (
                    order.name,
                    order.company_id.name,
                )
            )

            order.message_post(
                body=Markup(
                    "Intercompany Vendor Bill created in company <b>%s</b>."
                ) % (
                    company_to_bill.name,
                )
            )

            order.order_line._compute_qty_invoiced()
            order._get_invoiced()

        return True

    @api.depends("state", "order_line.qty_to_invoice")
    def _get_invoiced(self):

        super()._get_invoiced()

        precision = self.env[
            "decimal.precision"
        ].precision_get("Product Unit")

        for order in self.filtered(
            lambda o: o.is_intercompany_bill
        ):

            lines = order.order_line.filtered(
                lambda l: not l.display_type
            )

            if (
                lines
                and all(
                    float_is_zero(
                        line.qty_to_invoice,
                        precision_digits=precision,
                    )
                    for line in lines
                )
            ):
                order.invoice_status = "invoiced"
