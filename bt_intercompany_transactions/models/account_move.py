from odoo import _, fields, models
from odoo.exceptions import ValidationError
from markupsafe import Markup


class AccountMove(models.Model):
    _inherit = "account.move"

    intercompany_type = fields.Selection(
        [
            ("invoice", "Counterpart Invoice"),
            ("journal_entry", "Counterpart Journal Entry"),
        ],
        string="Intercompany Type",
        help="Specifies the type of intercompany transaction to create.",
        copy=False,
    )

    invoice_to_company_id = fields.Many2one(
        "res.company",
        string="Target Company"
    )

    counterpart_move_id = fields.Many2one(
        "account.move",
        string="Counterpart Document",
        copy=False,
        readonly=True,
        help="The counterpart invoice or journal entry created in the target company."
    )

    intercompany_source_move_id = fields.Many2one(
        "account.move",
        string="Intercompany Source Document",
        copy=False,
        readonly=True,
        help="Original intercompany vendor bill that generated this document."
    )

    def action_post(self):
        res = super().action_post()
        for move in self:
            if move.move_type != "in_invoice" or not move.intercompany_type:
                continue

            if move.intercompany_type == "invoice":
                if move.counterpart_move_id:
                    move._update_counterpart_invoice()
                else:
                    move._create_counterpart_invoice()
            elif move.intercompany_type == "journal_entry":
                if move.counterpart_move_id:
                    move._update_counterpart_journal_entry()
                else:
                    move._create_counterpart_journal_entry()
        return res

    def _stock_account_prepare_realtime_out_lines_vals(self):
        moves = self.filtered(
            lambda move: not (
                move.intercompany_source_move_id and move.move_type == 'out_invoice'
            )
        )
        return super(
            AccountMove,
            moves,
        )._stock_account_prepare_realtime_out_lines_vals()

    def button_draft(self):
        res = super().button_draft()
        for move in self:
            counterpart = move.counterpart_move_id
            if counterpart and counterpart.state != "draft":
                counterpart.button_draft()
        return res

    def button_cancel(self):
        res = super().button_cancel()
        for move in self:
            counterpart = move.counterpart_move_id
            if counterpart:
                if counterpart.state == "posted":
                    counterpart.button_draft()
                if counterpart.state != "cancel":
                    counterpart.button_cancel()
        return res

    def unlink(self):
        if self.env.context.get("skip_intercompany_counterpart_unlink"):
            return super().unlink()

        counterparts = (self.mapped("counterpart_move_id") - self).exists()

        for counterpart in counterparts:
            if counterpart.state == "posted":
                counterpart.button_draft()
            if counterpart.state != "cancel":
                counterpart.button_cancel()

        (self | counterparts).write({"counterpart_move_id": False})
        res = super().unlink()
        counterparts.with_context(skip_intercompany_counterpart_unlink=True).unlink()
        return res

    def _create_counterpart_invoice(self):
        self.ensure_one()
        if self.invoice_to_company_id == self.company_id:
            raise ValidationError(_("Target Company must be different from the current company."))

        invoice = self.env["account.move"].create({
            "move_type": "out_invoice",
            "intercompany_source_move_id": self.id,
            "partner_id": self.invoice_to_company_id.partner_id.id,
            "invoice_date": self.invoice_date,
            "invoice_origin": self.name,
            "currency_id": self.currency_id.id,
            "invoice_line_ids": self._prepare_counterpart_lines(),
            "company_id": self.company_id.id,
        })
        self.counterpart_move_id = invoice.id
        self.message_post(body=Markup(_("Counterpart Customer Invoice <b>%s</b> has been created.")) % invoice.display_name)
        invoice.message_post(body=Markup(_("Created from Vendor Bill <b>%s</b>.")) % self.display_name)
        return invoice

    def _update_counterpart_invoice(self):
        self.ensure_one()
        invoice = self.counterpart_move_id
        if not invoice:
            return

        if invoice.state != "draft":
            invoice.button_draft()

        invoice.invoice_line_ids.unlink()
        invoice.write({
            "partner_id": self.invoice_to_company_id.partner_id.id,
            "invoice_date": self.invoice_date,
            "currency_id": self.currency_id.id,
            "invoice_line_ids": self._prepare_counterpart_lines(),
        })
        self.message_post(body=Markup(_("Counterpart Customer Invoice <b>%s</b> has been updated.")) % invoice.display_name)
        invoice.message_post(body=Markup(_("Updated from Vendor Bill <b>%s</b>.")) % self.display_name)
        return invoice

    def _prepare_counterpart_lines(self):
        self.ensure_one()
        lines_data = []

        for line in self.invoice_line_ids.filtered(lambda l: l.display_type not in ('line_section', 'line_subsection', 'line_note')):
            line_vals = {
                "name": line.name,
                "quantity": line.quantity,
                "price_unit": line.price_unit,
                "tax_ids": [(6, 0, line.tax_ids.ids)],
            }

            if line.product_id:
                line_vals["product_id"] = line.product_id.id

            lines_data.append((0, 0, line_vals))
        return lines_data

    def _prepare_journal_entry_lines(self):
        self.ensure_one()
        lines_data = []
        total_credit_amount = 0.0
        total_price_total_bill_curr = 0.0

        journal = self.company_id.intercompany_journal_id
        if not journal:
            raise ValidationError(_("Please configure an 'Intercompany Journal' for company %s to create intercompany journal entries.") % self.company_id.name)

        date = self.invoice_date or self.date or fields.Date.context_today(self)
        company_currency = self.company_id.currency_id
        bill_currency = self.currency_id

        for line in self.invoice_line_ids.filtered(lambda l: l.display_type not in ('line_section', 'line_subsection', 'line_note')):
            # Use the account from the vendor bill line for the credit entry
            if not line.account_id:
                raise ValidationError(_("Missing account on invoice line for product %s.") % (line.product_id.name or line.name))

            line_price_total = line.price_total
            total_price_total_bill_curr += line_price_total

            line_credit_comp_curr = bill_currency._convert(
                line_price_total,
                company_currency,
                self.company_id,
                date
            )

            line_vals = {
                'name': line.name,
                'product_id': line.product_id.id,
                'account_id': line.account_id.id,
                'debit': 0.0,
                'credit': line_credit_comp_curr,
                'partner_id': self.invoice_to_company_id.partner_id.id,
            }
            if bill_currency != company_currency:
                line_vals.update({
                    'currency_id': bill_currency.id,
                    'amount_currency': -line_price_total,
                })

            lines_data.append((0, 0, line_vals))
            total_credit_amount += line_credit_comp_curr

        receivable_account = self.invoice_to_company_id.partner_id.property_account_receivable_id
        if not receivable_account:
            raise ValidationError(_("Please configure a Receivable Account for the partner of company %s in company %s.") % (self.invoice_to_company_id.name, self.company_id.name))

        rec_vals = {
            'name': _("Intercompany Receivable from %s") % self.invoice_to_company_id.name,
            'account_id': receivable_account.id,
            'debit': total_credit_amount,
            'credit': 0.0,
            'partner_id': self.invoice_to_company_id.partner_id.id,
        }
        if bill_currency != company_currency:
            rec_vals.update({
                'currency_id': bill_currency.id,
                'amount_currency': total_price_total_bill_curr,
            })

        lines_data.append((0, 0, rec_vals))
        return lines_data

    def _prepare_journal_entry_lines_for_target_company(self):
        self.ensure_one()
        lines_data = []
        total_other_expenses_amount_target_curr = 0.0
        total_other_expenses_amount_bill_curr = 0.0

        target_company = self.invoice_to_company_id
        
        if not target_company.intercompany_journal_id:
            raise ValidationError(_("Please configure an 'Intercompany Journal' for company %s to create intercompany journal entries.") % target_company.name)

        intercompany_expense_account = target_company.intercompany_expense_account_id
        if not intercompany_expense_account:
            raise ValidationError(_("Please configure an 'Intercompany Expense Account' in the company settings for company %s.") % target_company.name)

        date = self.invoice_date or self.date or fields.Date.context_today(self)
        target_currency = target_company.currency_id
        bill_currency = self.currency_id

        for line in self.invoice_line_ids.filtered(lambda l: l.display_type not in ('line_section', 'line_subsection', 'line_note')):
            line_price_total = line.price_total
            total_other_expenses_amount_bill_curr += line_price_total

            line_amount_target_curr = bill_currency._convert(
                line_price_total,
                target_currency,
                target_company,
                date
            )
            total_other_expenses_amount_target_curr += line_amount_target_curr

        exp_vals = {
            'name': _("Consolidated Intercompany Expense from %s") % self.company_id.name,
            'product_id': False,
            'account_id': intercompany_expense_account.id,
            'debit': total_other_expenses_amount_target_curr,
            'credit': 0.0,
            'partner_id': self.company_id.partner_id.with_company(target_company).id,
        }
        if bill_currency != target_currency:
            exp_vals.update({
                'currency_id': bill_currency.id,
                'amount_currency': total_other_expenses_amount_bill_curr,
            })

        lines_data.append((0, 0, exp_vals))

        total_debit_amount_all_lines = total_other_expenses_amount_target_curr

        payable_account = self.company_id.partner_id.with_company(target_company).property_account_payable_id
        if not payable_account:
            raise ValidationError(_("Please configure a Payable Account for the partner of company %s in company %s.") % (self.company_id.name, target_company.name))

        pay_vals = {
            'name': _("Intercompany Payable to %s") % self.company_id.name,
            'account_id': payable_account.id,
            'debit': 0.0,
            'credit': total_debit_amount_all_lines,
            'partner_id': self.company_id.partner_id.with_company(target_company).id,
        }
        if bill_currency != target_currency:
            pay_vals.update({
                'currency_id': bill_currency.id,
                'amount_currency': -total_other_expenses_amount_bill_curr,
            })

        lines_data.append((0, 0, pay_vals))
        return lines_data

    def _create_or_update_counterpart_journal_entry_in_target_company(self):
        self.ensure_one()
        target_company = self.invoice_to_company_id
        if target_company == self.company_id:
            raise ValidationError(_("Target company for counterpart journal entry must be different."))

        existing_je = self.env['account.move'].with_company(target_company)
        if self.name:
            existing_je = self.env['account.move'].with_company(target_company).search([
                ('move_type', '=', 'entry'),
                ('ref', '=', self.name),
                ('company_id', '=', target_company.id),
            ], limit=1)
        if not existing_je and self.ref:
            existing_je = self.env['account.move'].with_company(target_company).search([
                ('move_type', '=', 'entry'),
                ('ref', '=', self.ref),
                ('company_id', '=', target_company.id),
            ], limit=1)

        je_lines_data = self._prepare_journal_entry_lines_for_target_company()

        if existing_je:
            if existing_je.state != 'draft':
                existing_je.button_draft()

            existing_je.with_context(skip_delete_restriction=True).line_ids.unlink()
            existing_je.write({
                'date': self.invoice_date,
                'line_ids': je_lines_data,
            })
            counterpart_je = existing_je
            je_name_for_message = counterpart_je.display_name

            msg_orig = Markup(_("Intercompany Journal Entry <b>%s</b> updated in %s.")) % (je_name_for_message, target_company.name)
            msg_counter = Markup(_("Updated from Vendor Bill <b>%s</b> (Company: %s).")) % (self.display_name, self.company_id.name)
        else:
            target_journal = target_company.intercompany_journal_id
            if not target_journal:
                raise ValidationError(_("Please configure an 'Intercompany Journal' for company %s.") % target_company.name)

            counterpart_je = self.env['account.move'].with_company(target_company).create({
                'move_type': 'entry',
                'ref': self.name if self.name else self.ref,
                'date': self.invoice_date,
                'journal_id': target_journal.id,
                'line_ids': je_lines_data,
                'company_id': target_company.id,
            })
            je_name_for_message = counterpart_je.ref

            msg_orig = Markup(_("Intercompany Journal Entry created in %s with ref <b>%s</b>")) % (target_company.name, je_name_for_message)
            msg_counter = Markup(_("Created from Vendor Bill <b>%s</b> (Company: %s).")) % (self.display_name, self.company_id.name)

        self.message_post(body=msg_orig)
        counterpart_je.message_post(body=msg_counter)
        return counterpart_je

    def _create_counterpart_journal_entry(self):
        self.ensure_one()
        if self.invoice_to_company_id == self.company_id:
            raise ValidationError(_("Target Company must be different from the current company."))

        journal = self.company_id.intercompany_journal_id
        if not journal:
            raise ValidationError(_("Please configure an 'Intercompany Journal' for company %s.") % self.company_id.name)

        journal_entry = self.env["account.move"].create({
            "move_type": "entry",
            "ref": self.name,
            "date": self.invoice_date,
            "journal_id": journal.id,
            "line_ids": self._prepare_journal_entry_lines(),
            "company_id": self.company_id.id,
        })
        self.counterpart_move_id = journal_entry.id
        journal_entry.message_post(body=Markup(_("Created from Vendor Bill <b>%s</b>.")) % self.display_name)
        journal_entry.action_post()
        self.message_post(body=Markup(_("Counterpart Journal Entry <b>%s</b> created in %s.")) % (journal_entry.display_name, self.company_id.name))

        self._create_or_update_counterpart_journal_entry_in_target_company()
        return journal_entry

    def _update_counterpart_journal_entry(self):
        self.ensure_one()
        je = self.counterpart_move_id
        if not je:
            return

        if je.state != "draft":
            je.button_draft()

        je.with_context(skip_delete_restriction=True).line_ids.unlink()
        je.write({
            "ref": self.name,
            "date": self.invoice_date,
            "line_ids": self._prepare_journal_entry_lines(),
        })
        je.message_post(body=Markup(_("Updated from Vendor Bill <b>%s</b>.")) % self.display_name)
        self.message_post(body=Markup(_("Counterpart Journal Entry <b>%s</b> updated in %s.")) % (je.display_name, self.company_id.name))
        je.action_post()
        self._create_or_update_counterpart_journal_entry_in_target_company()
        return je
