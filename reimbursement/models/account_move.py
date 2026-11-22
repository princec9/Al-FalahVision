import typing

from odoo import models, fields, api, _
from odoo.api import ValuesType
from odoo.exceptions import UserError
from odoo import fields, models
from odoo import fields, models, _
from odoo.exceptions import UserError



class AccountJournal(models.Model):
    _inherit = "account.journal"

    is_insurance = fields.Boolean(
        string="Is Insurance",
        default=False,
        copy=True,
        help="Mark this journal as an insurance payment journal.",
    )


class InvoiceExpenseWizard(models.TransientModel):
    _name = "invoice.expense.wizard"
    _description = "Invoice Expense Wizard"

    invoice_id = fields.Many2one(
        "account.move",
        string="Invoice",
        required=True,
        readonly=True,
    )

    amount = fields.Monetary(
        string="Expense Amount",
        required=True,
        currency_field="currency_id",
    )

    company_id = fields.Many2one(
        related="invoice_id.company_id",
        readonly=True,
    )

    payment_journal_id = fields.Many2one(
        "account.journal",
        string="Paid From",
        required=True,
        domain="[('is_expense_journal', '=', True)]",
    )

    currency_id = fields.Many2one(
        related="invoice_id.currency_id",
        readonly=True,
    )

    def action_create_expense(self):
        self.ensure_one()

        invoice = self.invoice_id

        if not invoice:
            raise UserError(_("Invoice is required."))

        if invoice.move_type != "out_invoice":
            raise UserError(
                _("Expense can only be created from a customer invoice.")
            )

        if invoice.state != "posted":
            raise UserError(
                _("Expense can only be created from a posted invoice.")
            )

        if invoice.expense_move_id:
            raise UserError(
                _("An expense journal entry has already been created.")
            )

        if self.amount <= 0:
            raise UserError(
                _("Expense amount must be greater than zero.")
            )

        # Get configuration
        params = self.env["ir.config_parameter"].sudo()

        journal_id = params.get_param(
            "reimbursement.expense_journal_id"
        )

        account_id = params.get_param(
            "reimbursement.expense_account_id"
        )

        if not journal_id:
            raise UserError(
                _("Please configure the Expense Journal in Accounting Settings.")
            )

        if not account_id:
            raise UserError(
                _("Please configure the Expense Account in Accounting Settings.")
            )

        journal = self.env["account.journal"].browse(int(journal_id))
        expense_account = self.env["account.account"].browse(int(account_id))

        if not journal.exists():
            raise UserError(
                _("The configured Expense Journal does not exist.")
            )

        if not expense_account.exists():
            raise UserError(
                _("The configured Expense Account does not exist.")
            )

        # ------------------------------------------------------------
        # CHANGED: credit account comes from the journal chosen in the
        # wizard ("Paid From"), not from the Expense Journal's default
        # account.
        # ------------------------------------------------------------
        if not self.payment_journal_id:
            raise UserError(_("Please select the 'Paid From' journal."))

        default_account = self.payment_journal_id.default_account_id

        if not default_account:
            raise UserError(
                _("The selected journal '%s' has no Journal Account configured.")
                % self.payment_journal_id.display_name
            )

        # Create Journal Entry
        expense_move = self.env["account.move"].create({
            "move_type": "entry",
            "journal_id": self.payment_journal_id.id,
            "date": fields.Date.context_today(self),
            # "ref": _("Expense for Invoice %s") % invoice.name,

            "ref": _("Expense"),
            "narration": _("Invoice : %s") % invoice.name,
            "line_ids": [
                (
                    0,
                    0,
                    {
                        "name": _("Expense "),
                        # "name": _("Expense - %s") % invoice.name,
                        "account_id": expense_account.id,
                        "debit": self.amount,
                        "credit": 0.0,
                        "partner_id": invoice.partner_id.id,
                    },
                ),
                (
                    0,
                    0,
                    {"name": _("Expense "),
                        # "name": _("Expense - %s") % invoice.name,
                        "account_id": default_account.id,
                        "debit": 0.0,
                        "credit": self.amount,
                        "partner_id": invoice.partner_id.id,
                    },
                ),
            ],
        })

        # Post the Journal Entry
        expense_move.action_post()

        # Link it to invoice
        invoice.expense_move_id = expense_move.id

        return {
            "type": "ir.actions.act_window",
            "name": _("Expense Journal Entry"),
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": expense_move.id,
            "target": "current",
        }

class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    expense_journal_id = fields.Many2one(
        "account.journal",
        string="Expense Journal",
        domain="[('type', '=', 'general')]",
        config_parameter="reimbursement.expense_journal_id",
    )

    expense_account_id = fields.Many2one(
        "account.account",
        string="Expense Account",
        domain="[('account_type', '=', 'expense')]",
        config_parameter="reimbursement.expense_account_id",
    )
class AccountMove(models.Model):
    _inherit = "account.move"
    delivery_id = fields.Many2one(
        "stock.picking",
        string="Delivery Order",
        readonly=True,
        copy=False,
    )
    expense_move_id = fields.Many2one(
        "account.move",
        string="Expense Journal Entry",
        readonly=True,
        copy=False,
    )

    expense_move_count = fields.Integer(
        string="Expense",
        compute="_compute_expense_move_count",
    )

    def _compute_expense_move_count(self):
        for move in self:
            move.expense_move_count = 1 if move.expense_move_id else 0

    def action_view_expense_move(self):
        self.ensure_one()

        if not self.expense_move_id:
            return False

        return {
            "type": "ir.actions.act_window",
            "name": _("Expense Journal Entry"),
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": self.expense_move_id.id,
            "target": "current",
        }

    def action_open_expense_wizard(self):
        self.ensure_one()

        if self.move_type != "out_invoice":
            raise UserError(
                _("Expense can only be created from a customer invoice.")
            )

        if self.state != "posted":
            raise UserError(
                _("Expense can only be created from a posted invoice.")
            )

        if self.expense_move_id:
            raise UserError(
                _("An expense journal entry has already been created.")
            )

        return {
            "type": "ir.actions.act_window",
            "name": _("Create Expense"),
            "res_model": "invoice.expense.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_invoice_id": self.id,
            },
        }
    # def action_post(self):
    #     res = super().action_post()
    #
    #     for invoice in self:
    #         if invoice.move_type == "out_invoice":
    #             invoice._create_invoice_delivery()
    #
    #     return res
    is_direct_invoice = fields.Boolean(
        compute="_compute_is_direct_invoice"
    )

    @api.depends("invoice_line_ids.sale_line_ids")
    def _compute_is_direct_invoice(self):
        for move in self:
            move.is_direct_invoice = (
                    move.move_type == "out_invoice"
                    and not move.invoice_line_ids.mapped("sale_line_ids")
            )

    def create_invoice_delivery(self):
        self.ensure_one()

        # Only stockable products from the invoice
        invoice_lines = self.invoice_line_ids.filtered(
            lambda line:
            line.product_id
            and line.product_id.is_storable
            and line.quantity > 0
        )

        if not invoice_lines:
            return

        # Prevent duplicate delivery
        if self.delivery_id:
            return

        # Find warehouse
        warehouse = self.env["stock.warehouse"].search(
            [
                ("company_id", "=", self.company_id.id),
            ],
            limit=1,
        )

        if not warehouse:
            raise UserError(
                _("No warehouse found for company %s.")
                % self.company_id.display_name
            )

        picking_type = warehouse.out_type_id

        if not picking_type:
            raise UserError(
                _("No outgoing operation type found for the warehouse.")
            )

        source_location = picking_type.default_location_src_id
        destination_location = self.partner_id.property_stock_customer

        # =========================================================
        # CREATE DELIVERY
        # =========================================================
        picking = self.env["stock.picking"].create({
            "picking_type_id": picking_type.id,
            "location_id": source_location.id,
            "location_dest_id": destination_location.id,
            "partner_id": self.partner_id.id,
            "origin": self.name,
            "company_id": self.company_id.id,
            "move_type": "direct",
        })

        # =========================================================
        # CREATE STOCK MOVES
        # =========================================================
        for line in invoice_lines:
            self.env["stock.move"].create({
                # "name": line.name or line.product_id.display_name,
                "product_id": line.product_id.id,
                "product_uom_qty": line.quantity,
                "product_uom": line.product_uom_id.id,
                "picking_id": picking.id,
                "location_id": source_location.id,
                "location_dest_id": destination_location.id,
                "company_id": self.company_id.id,
            })

        # =========================================================
        # CONFIRM DELIVERY
        # =========================================================
        picking.action_confirm()

        # =========================================================
        # RESERVE STOCK
        # =========================================================
        picking.action_assign()

        # =========================================================
        # CHECK STOCK
        # =========================================================
        unavailable_moves = picking.move_ids.filtered(
            lambda move: move.state != "assigned"
        )

        if unavailable_moves:
            picking.unlink()

            raise UserError(
                _(
                    "There is not enough stock available for invoice %s."
                )
                % self.name
            )

        # =========================================================
        # SET DONE QUANTITY
        # =========================================================
        for move in picking.move_ids:
            for move_line in move.move_line_ids:
                move_line.quantity = move_line.quantity

        # =========================================================
        # VALIDATE DELIVERY
        # =========================================================
        result = picking.button_validate()

        # =========================================================
        # HANDLE IMMEDIATE TRANSFER
        # =========================================================
        if isinstance(result, dict):
            if result.get("res_model") == "stock.immediate.transfer":
                wizard = self.env["stock.immediate.transfer"].browse(
                    result["res_id"]
                )
                wizard.process()

        # =========================================================
        # LINK DELIVERY TO INVOICE
        # =========================================================
        self.delivery_id = picking.id

    insurance_applicable = fields.Boolean(
        string="Insurance",
        default=False,
        tracking=True,
    )
    insurance_agency_id = fields.Many2one(
        "res.partner",
        string="Insurance Agency",
        domain="[('is_insurance', '=', True)]", tracking=True
    )

    customer_id = fields.Many2one(
        "res.partner",
        string="Customer",
        domain="[('is_insurance', '=', False)]",tracking=True
    )
    claim_amount= fields.Monetary(string="Claim Amount")
    claim_move_id = fields.Many2one(
        "account.move",
        string="Claim Journal Entry",
        readonly=True,
        copy=False,
    )

    claim_move_count = fields.Integer(
        compute="_compute_claim_move_count"
    )
    # def action_create_claim(self):
    #     self.ensure_one()
    #
    #     if not self.insurance_agency_id:
    #         raise UserError(_("Please select Insurance Agency."))
    #
    #     if self.claim_amount <= 0:
    #         raise UserError(_("Please enter Claim Amount."))
    #
    #     if self.claim_move_id:
    #         raise UserError(_("Claim Journal already exists."))
    #
    #     journal = self.env["account.journal"].search([
    #         ("type", "=", "general"),
    #         ("company_id", "=", self.company_id.id),
    #     ], limit=1)
    #
    #     if not journal:
    #         raise UserError(_("No General Journal found."))
    #
    #     customer_receivable = self.partner_id.property_account_receivable_id
    #     insurance_receivable = (
    #         self.insurance_agency_id.property_account_receivable_id
    #     )
    #
    #     # Create Insurance Claim Journal Entry
    #     move = self.env["account.move"].create({
    #         "move_type": "entry",
    #         "journal_id": journal.id,
    #         "date": fields.Date.context_today(self),
    #         "ref": f"{self.name} - Insurance Claim",
    #         "insurance_agency_id": self.insurance_agency_id.id,
    #         "customer_id": self.partner_id.id,
    #         "claim_amount": self.claim_amount,
    #         "line_ids": [
    #             (0, 0, {
    #                 "name": "Insurance Claim",
    #                 "partner_id": self.insurance_agency_id.id,
    #                 "account_id": insurance_receivable.id,
    #                 "debit": self.claim_amount,
    #             }),
    #             (0, 0, {
    #                 "name": "Insurance Claim",
    #                 "partner_id": self.partner_id.id,
    #                 "account_id": customer_receivable.id,
    #                 "credit": self.claim_amount,
    #             }),
    #         ],
    #     })
    #
    #     # Post the Insurance Claim Journal Entry
    #     move.action_post()
    #
    #     # Link claim journal to invoice
    #     self.claim_move_id = move.id
    #
    #     # Post the Customer Invoice ONLY when:
    #     # 1. Invoice is still Draft
    #     # 2. Reimbursement is enabled
    #     #
    #     # If invoice is already Posted, action_post() is NOT called.
    #     if self.state == "draft":
    #         self.action_post()
    #
    #     # Reconcile Customer Receivable
    #     claim_line = move.line_ids.filtered(
    #         lambda l: (
    #                 l.partner_id == self.partner_id
    #                 and l.account_id == customer_receivable
    #                 and l.credit > 0
    #                 and not l.reconciled
    #         )
    #     )[:1]
    #
    #     if claim_line:
    #         self.js_assign_outstanding_line(claim_line.id)
    #
    #     return {
    #         "effect": {
    #             "fadeout": "slow",
    #             "message": _("Insurance Claim Created"),
    #             "type": "rainbow_man",
    #         }
    #     }
    def action_create_claim(self):
        self.ensure_one()

        if not self.insurance_agency_id:
            raise UserError(_("Please select Insurance Agency."))

        if self.claim_amount <= 0:
            raise UserError(_("Please enter Claim Amount."))

        if self.claim_move_id:
            raise UserError(_("Claim Journal already exists."))

        # ---------------------------------------------------------
        # POST INVOICE FIRST
        # ---------------------------------------------------------
        if self.state == "draft":
            self.action_post()

        # Reload current invoice record
        self.invalidate_recordset()
        self.env.cr.flush()

        # ---------------------------------------------------------
        # NOW self.name should contain the posted invoice number
        # ---------------------------------------------------------
        invoice_ref = self.name

        if not invoice_ref:
            raise UserError(
                _("Invoice number is not available. Please try again.")
            )

        journal = self.env["account.journal"].search([
            ("type", "=", "general"),
            ("company_id", "=", self.company_id.id),
        ], limit=1)

        if not journal:
            raise UserError(_("No General Journal found."))

        customer_receivable = self.partner_id.property_account_receivable_id
        insurance_receivable = (
            self.insurance_agency_id.property_account_receivable_id
        )

        # ---------------------------------------------------------
        # CREATE CLAIM JOURNAL
        # ---------------------------------------------------------
        move = self.env["account.move"].create({
            "move_type": "entry",
            "journal_id": journal.id,
            "date": fields.Date.context_today(self),

            # IMPORTANT
            "ref": f"{invoice_ref} - Insurance Claim",

            "insurance_agency_id": self.insurance_agency_id.id,
            "customer_id": self.partner_id.id,
            "claim_amount": self.claim_amount,

            "line_ids": [
                (0, 0, {
                    "name": "Insurance Claim",
                    "partner_id": self.insurance_agency_id.id,
                    "account_id": insurance_receivable.id,
                    "debit": self.claim_amount,
                }),
                (0, 0, {
                    "name": "Insurance Claim",
                    "partner_id": self.partner_id.id,
                    "account_id": customer_receivable.id,
                    "credit": self.claim_amount,
                }),
            ],
        })

        move.action_post()

        self.claim_move_id = move.id

        # ---------------------------------------------------------
        # RECONCILE CUSTOMER RECEIVABLE
        # ---------------------------------------------------------
        claim_line = move.line_ids.filtered(
            lambda l: (
                    l.partner_id == self.partner_id
                    and l.account_id == customer_receivable
                    and l.credit > 0
                    and not l.reconciled
            )
        )[:1]

        if claim_line:
            self.js_assign_outstanding_line(claim_line.id)

        return {
            "effect": {
                "fadeout": "slow",
                "message": _("Insurance Claim Created"),
                "type": "rainbow_man",
            }
        }
    def action_view_claim_journal(self):
        self.ensure_one()

        if not self.claim_move_id:
            return False

        return {
            "type": "ir.actions.act_window",
            "name": _("Claim Journal"),
            "res_model": "account.move",
            "res_id": self.claim_move_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def _compute_claim_move_count(self):
        for rec in self:
            rec.claim_move_count = 1 if rec.claim_move_id else 0

    # def action_create_claim(self):
    #     self.ensure_one()
    #
    #     if not self.insurance_agency_id:
    #         raise UserError(_("Please select Insurance Agency."))
    #
    #     if self.claim_amount <= 0:
    #         raise UserError(_("Please enter Claim Amount."))
    #
    #     if self.claim_move_id:
    #         raise UserError(_("Claim Journal already exists."))
    #
    #     journal = self.env["account.journal"].search([
    #         ("type", "=", "general"),
    #         ("company_id", "=", self.company_id.id),
    #     ], limit=1)
    #
    #     if not journal:
    #         raise UserError(_("No General Journal found."))
    #
    #     customer_receivable = self.partner_id.property_account_receivable_id
    #     insurance_receivable = self.insurance_agency_id.property_account_receivable_id
    #
    #     move = self.env["account.move"].create({
    #         "move_type": "entry",
    #         "journal_id": journal.id,
    #         "date": fields.Date.context_today(self),
    #         "ref": _("Insurance Claim"),
    #         # "ref": f"{self.name} - Insurance Claim",
    #         "insurance_agency_id": self.insurance_agency_id.id,
    #         "customer_id": self.partner_id.id,
    #         "claim_amount": self.claim_amount,
    #         "line_ids": [
    #             (0, 0, {
    #                 "name": "Insurance Claim",
    #                 "partner_id": self.insurance_agency_id.id,
    #                 "account_id": insurance_receivable.id,
    #                 "debit": self.claim_amount,
    #             }),
    #             (0, 0, {
    #                 "name": "Insurance Claim",
    #                 "partner_id": self.partner_id.id,
    #                 "account_id": customer_receivable.id,
    #                 "credit": self.claim_amount,
    #             }),
    #         ],
    #     })
    #
    #     move.action_post()
    #
    #     self.claim_move_id = move.id
    #
    #     if self.state == 'draft':
    #         self.action_post()
    #     move.ref = f"{self.name} - Insurance Claim"
    #     claim_line = move.line_ids.filtered(
    #         lambda l: (
    #                 l.partner_id == self.partner_id
    #                 and l.account_id == customer_receivable
    #                 and l.credit > 0
    #                 and not l.reconciled
    #         )
    #     )[:1]
    #     if claim_line:
    #         self.js_assign_outstanding_line(claim_line.id)
    #
    #     return {
    #         "effect": {
    #             "fadeout": "slow",
    #             "message": _("Insurance Claim Created"),
    #             "type": "rainbow_man",
    #         }
    #
    #     }




    is_reimbursement = fields.Boolean("Is Reimbursement")
    account_reimbursement_line_ids = fields.One2many("account.reimbursement.line", "move_id")

    re_amount_total = fields.Monetary(string="Total", compute='_compute_amount', store=True, readonly=True,
                                      currency_field='currency_id', )
    re_tax_amount = fields.Monetary(string="Tax", compute='_compute_amount', store=True, readonly=True,
                                    currency_field='currency_id', )
    re_grand_total = fields.Monetary(string="Gross Total", compute='_compute_amount', store=True, readonly=True,
                                     currency_field='currency_id', )
    re_invoice_payments_widget = fields.Html(compute='_get_re_invoice_payments_widget', exportable=False)



    def _get_re_invoice_payments_widget(self):
        for rec in self:
            rec.re_invoice_payments_widget = (
                '<i class="fa fa-info-circle" style="color:#17a2b8;margin-right:5px;"></i>'
                'Paid on {} <span style="margin-left:15px;">{:,.2f} AED</span>'
            ).format(
                # rec.create_date.strftime("%d/%m/%Y"),
                rec.create_date.strftime("%d/%m/%Y") if rec.create_date else '',
                round(rec.re_grand_total or 0.0, 2)
            )

    @api.depends(
        'line_ids.matched_debit_ids.debit_move_id.move_id.origin_payment_id.is_matched',
        'line_ids.matched_debit_ids.debit_move_id.move_id.line_ids.amount_residual',
        'line_ids.matched_debit_ids.debit_move_id.move_id.line_ids.amount_residual_currency',
        'line_ids.matched_credit_ids.credit_move_id.move_id.origin_payment_id.is_matched',
        'line_ids.matched_credit_ids.credit_move_id.move_id.line_ids.amount_residual',
        'line_ids.matched_credit_ids.credit_move_id.move_id.line_ids.amount_residual_currency',
        'line_ids.balance',
        'line_ids.currency_id',
        'line_ids.amount_currency',
        'line_ids.amount_residual',
        'line_ids.amount_residual_currency',
        'line_ids.payment_id.state',
        'line_ids.full_reconcile_id',
        'state',
        'company_id', 'account_reimbursement_line_ids', 'is_reimbursement',
        'account_reimbursement_line_ids.price_subtotal', 'account_reimbursement_line_ids.price_total')
    def _compute_amount(self):
        super()._compute_amount()
        for rec in self:
            if not rec.is_reimbursement:
                rec.re_amount_total = rec.amount_untaxed_in_currency_signed
                rec.re_tax_amount = rec.amount_total_in_currency_signed - rec.amount_untaxed_in_currency_signed
                rec.re_grand_total = rec.amount_total_in_currency_signed
            else:
                amount_total = sum(x.price_subtotal for x in rec.account_reimbursement_line_ids)
                grand_amount_total = sum(x.price_total for x in rec.account_reimbursement_line_ids)
                rec.re_amount_total = amount_total or 0
                rec.re_tax_amount = grand_amount_total - amount_total
                rec.re_grand_total = grand_amount_total

    def action_generate_reimbursement(self):
        """ANAND MC JUN 30 2025"""

        for rec in self:
            rec.is_reimbursement = True


class AccountJournal(models.Model):
    _inherit = "account.journal"

    is_expense_journal = fields.Boolean(
        string="Use for Expenses",
        help="If checked, this journal can be selected as 'Paid From' "
             "in the Create Expense wizard.",
    )
