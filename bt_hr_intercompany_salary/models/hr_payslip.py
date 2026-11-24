import logging

from markupsafe import Markup

from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools import float_is_zero, formatLang

_logger = logging.getLogger(__name__)


class HrPayslip(models.Model):
    _inherit = 'hr.payslip'

    allocation_line_ids = fields.One2many(
        'hr.payslip.allocation.line',
        'payslip_id',
        string='Salary Allocation',
        copy=True,
    )
    total_allocation_percentage = fields.Float(
        string='Total Allocation (%)',
        compute='_compute_total_allocation_percentage',
        digits=(5, 2),
    )

    intercompany_invoice_ids = fields.Many2many(
        'account.move',
        'payslip_intercompany_invoice_rel',
        'payslip_id',
        'invoice_id',
        string='Intercompany Journal Entries',
        copy=False,
        readonly=True,
    )
    intercompany_invoice_count = fields.Integer(
        string='Intercompany Journal Entry Count',
        compute='_compute_intercompany_invoice_count',
    )
    intercompany_status = fields.Selection(
        selection=[
            ('na', 'Not Applicable'),
            ('pending', 'Pending Generation'),
            ('generated', 'Journal Entries Generated'),
            ('cancelled', 'Journal Entries Cancelled'),
        ],
        string='Intercompany Status',
        compute='_compute_intercompany_status',
        store=True,
    )

    @api.depends('allocation_line_ids.contribution_percentage')
    def _compute_total_allocation_percentage(self):
        for slip in self:
            slip.total_allocation_percentage = sum(
                slip.allocation_line_ids.mapped('contribution_percentage')
            )

    @api.depends('intercompany_invoice_ids')
    def _compute_intercompany_invoice_count(self):
        for slip in self:
            slip.intercompany_invoice_count = len(slip.intercompany_invoice_ids)

    @api.depends(
        'state',
        'employee_id.is_multi_company_employee',
        'intercompany_invoice_ids',
        'intercompany_invoice_ids.state',
    )
    def _compute_intercompany_status(self):
        for slip in self:
            emp = slip.employee_id
            if not emp.is_multi_company_employee:
                slip.intercompany_status = 'na'
            elif slip.state == 'validated' and not slip.intercompany_invoice_ids:
                slip.intercompany_status = 'pending'
            elif slip.intercompany_invoice_ids:
                cancelled_all = all(
                    move.state == 'cancel'
                    for move in slip.intercompany_invoice_ids
                )
                slip.intercompany_status = 'cancelled' if cancelled_all else 'generated'
            else:
                slip.intercompany_status = 'na'

    @api.onchange('employee_id')
    def _onchange_employee_intercompany(self):
        if not self.employee_id or not self.employee_id.is_multi_company_employee:
            return
        self._populate_allocation_lines()

    def compute_sheet(self):
        res = super().compute_sheet()
        for slip in self:
            if slip.allocation_line_ids:
                slip.allocation_line_ids.recompute_amount()
        return res

    def action_refresh_allocation_amounts(self):
        self.ensure_one()
        if not self.allocation_line_ids:
            return
        self.allocation_line_ids.recompute_amount()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Amounts Refreshed'),
                'message': _('Allocation amounts updated from current salary computation.'),
                'type': 'success',
                'sticky': False,
            }
        }

    def _populate_allocation_lines(self):
        for slip in self:
            if slip.allocation_line_ids:
                continue
            employee = slip.employee_id
            if not employee.is_multi_company_employee:
                continue
            lines = []
            for contrib in employee.get_active_contributions():
                lines.append((0, 0, {
                    'company_id': contrib.company_id.id,
                    'contribution_percentage': contrib.contribution_percentage,
                    'intercompany_partner_id': contrib.intercompany_partner_id.id
                        if contrib.intercompany_partner_id else False,
                }))
            if lines:
                slip.allocation_line_ids = lines

    def action_payslip_done(self):
        res = super().action_payslip_done()
        self._generate_intercompany_invoices()
        return res

    def action_payslip_cancel(self):
        for slip in self:
            slip._cancel_intercompany_invoices(reason=_('Payslip cancelled'))
        return super().action_payslip_cancel()

    def action_payslip_draft(self):
        for slip in self:
            slip._cancel_intercompany_invoices(reason=_('Payslip reset to draft'))
            slip.allocation_line_ids.write({'invoice_id': False})
        return super().action_payslip_draft()

    def _generate_intercompany_invoices(self):
        for slip in self.filtered(lambda s: s.state == 'validated'):
            employee = slip.employee_id
            if not employee.is_multi_company_employee:
                _logger.info('Payslip %s: Employee not multi-company. Skipping.', slip.name)
                continue

            if slip.intercompany_invoice_ids.filtered(lambda m: m.state != 'cancel'):
                _logger.warning(
                    'Payslip %s already has active intercompany journal entries. Skipping.',
                    slip.name
                )
                continue

            allocation_lines = slip.allocation_line_ids
            if not allocation_lines:
                slip._populate_allocation_lines()
                allocation_lines = slip.allocation_line_ids

            lines_to_process = allocation_lines.filtered(
                lambda l: l.company_id != slip.company_id
            )
            if not lines_to_process:
                _logger.info(
                    'Payslip %s: No external allocation lines found. Skipping.',
                    slip.name
                )
                continue

            total_salary = slip._get_total_salary_amount()
            _logger.info(
                'Payslip %s: Total salary = %.2f | Creating journal entries for %d companies',
                slip.name, total_salary, len(lines_to_process)
            )
            if float_is_zero(total_salary, precision_digits=2):
                _logger.warning('Payslip %s: Total salary is zero. Skipping.', slip.name)
                continue

            created_moves = self.env['account.move']
            for line in lines_to_process:
                created_moves |= slip._create_intercompany_moves_for_line(line, total_salary)

            if created_moves:
                slip.write({
                    'intercompany_invoice_ids': [(4, move.id) for move in created_moves]
                })
                slip._log_generation_summary(created_moves)
                _logger.info(
                    'Payslip %s: Created %d draft intercompany journal entry(s).',
                    slip.name, len(created_moves)
                )

    def _create_intercompany_moves_for_line(self, line, total_salary):
        """Create the source and counterpart draft entries for one line.

        Both entries are created in ``draft`` state (never auto-posted) so
        they can be reviewed before posting. Returns the combined recordset
        of created moves.
        """
        self.ensure_one()

        amount = round(total_salary * (line.contribution_percentage / 100.0), 2)
        if float_is_zero(amount, precision_digits=2):
            _logger.warning(
                'Allocation amount is zero for %s. Skipping.',
                line.company_id.sudo().name
            )
            return self.env['account.move']

        line.amount = amount

        source_move = self._create_intercompany_source_move(line, amount)
        if not source_move:
            return self.env['account.move']

        line.invoice_id = source_move.id
        source_move._log_intercompany_origin()

        source_move._create_intercompany_counterpart_move(amount=amount)

        return source_move

    def _create_intercompany_source_move(self, line, amount):
        """Create the draft source entry in the paying (parent) company.

        Debits an intercompany receivable and credits income. The entry is
        deliberately left in ``draft``; posting is a manual, reviewed step.
        """
        slip = self
        parent_company = slip.company_id
        target_company = line.company_id
        target_company_name = target_company.sudo().name

        target_partner = line.sudo().get_partner_for_invoice()
        if not target_partner:
            raise UserError(_(
                'No partner found for company %(company)s. Please configure '
                'the intercompany partner.',
                company=target_company_name,
            ))

        income_account = self._get_credit_account(parent_company)
        receivable_account = target_partner.property_account_receivable_id

        if not income_account:
            raise UserError(_(
                'No income account configured for company %(company)s. '
                'Please set one in Configuration -> Settings.',
                company=parent_company.name,
            ))
        if not receivable_account:
            raise UserError(_(
                'No receivable account configured for partner %(partner)s in '
                'company %(company)s. Please set one on the partner record.',
                partner=target_partner.name, company=parent_company.name,
            ))

        journal = self._get_general_journal(parent_company)
        if not journal:
            raise UserError(_(
                'No general journal found in company %(company)s.',
                company=parent_company.name,
            ))

        narration = _(
            'Intercompany Salary Cost Allocation\n'
            'Period: %(start)s to %(end)s\n'
            'Contribution: %(pct).2f%%',
            start=slip.date_from,
            end=slip.date_to,
            pct=line.contribution_percentage,
        )

        move_vals = {
            'move_type': 'entry',
            'company_id': parent_company.id,
            'journal_id': journal.id,
            'date': slip.date_to,
            'ref': 'SALARY-ALLOC-%s' % (
                target_company_name[:10].upper().replace(' ', '_'),
            ),
            'narration': narration,
            'line_ids': [
                (0, 0, {
                    'name': _('Intercompany Receivable - %s', target_company_name),
                    'account_id': receivable_account.id,
                    'partner_id': target_partner.id,
                    'debit': amount,
                    'credit': 0.0,
                }),
                (0, 0, {
                    'name': _('Intercompany Income - %s', target_company_name),
                    'account_id': income_account.id,
                    'partner_id': target_partner.id,
                    'debit': 0.0,
                    'credit': amount,
                }),
            ],
            'intercompany_payslip_id': slip.id,
            'intercompany_source_company_id': parent_company.id,
            'intercompany_target_company_id': target_company.id,
            'is_intercompany_salary_move': True,
            'intercompany_role': 'source',
        }

        with self.env.cr.savepoint():
            source_move = self.env['account.move'].with_company(
                parent_company
            ).create(move_vals)
            _logger.info(
                'Created draft source journal entry in %s for %s (%.2f).',
                parent_company.name, target_company_name, amount
            )

        return source_move

    def _get_total_salary_amount(self):
        self.ensure_one()
        if self.version_id and self.version_id.wage:
            _logger.info(
                'Payslip %s: Using version wage = %.2f', self.name, self.version_id.wage
            )
            return self.version_id.wage

        _logger.warning('Payslip %s: No wage found. Returning 0.', self.name)
        return 0.0

    def _get_related_intercompany_moves(self):
        """Return every move linked to this payslip (source + counterpart).

        Uses both the stored many2many link and a defensive search on
        ``intercompany_payslip_id`` (with ``sudo`` so counterpart entries in
        other companies are always included), then de-duplicates.
        """
        self.ensure_one()
        Move = self.env['account.move'].sudo()
        linked = self.intercompany_invoice_ids.sudo()
        searched = Move.search([
            ('intercompany_payslip_id', '=', self.id),
        ])
        return linked | searched

    def _cancel_intercompany_invoices(self, reason=None):
        """Delete all journal entries related to the payslip.

        Related entries (source + counterpart, across companies) are unlinked.
        Posted entries are reset to draft first so they can be removed. The
        unlink action is recorded in the payslip chatter.
        """
        reason = reason or _('Payslip cancelled')
        for slip in self:
            moves = slip._get_related_intercompany_moves()
            if not moves:
                _logger.info('Payslip %s: No related journal entries to unlink.', slip.name)
                continue

            entry_labels = []
            for move in moves:
                amount = sum(move.line_ids.mapped('debit'))
                entry_labels.append('%s (%s): %s' % (
                    move.name or _('Draft'),
                    move.company_id.name,
                    formatLang(self.env, amount, currency_obj=move.company_id.currency_id),
                ))

            _logger.info(
                'Payslip %s: Unlinking %d related journal entry(s). Reason: %s',
                slip.name, len(moves), reason
            )

            try:
                posted = moves.filtered(lambda m: m.state == 'posted')
                if posted:
                    posted.button_draft()
                moves.unlink()
            except Exception as exc:
                _logger.error(
                    'Failed to unlink journal entries for payslip %s: %s',
                    slip.name, exc
                )
                continue

            rows = Markup('').join(
                Markup('<li>%s</li>') % label for label in entry_labels
            )
            slip.message_post(
                body=(
                    Markup('<p>%s</p>') % _(
                        '%(count)d related intercompany journal entry(s) '
                        'unlinked. Reason: %(reason)s',
                        count=len(entry_labels), reason=reason,
                    )
                    + Markup('<ul>%s</ul>') % rows
                ),
                subtype_xmlid='mail.mt_note',
            )

    def _log_generation_summary(self, source_moves):
        """Post a summary note on the payslip listing generated entries.

        ``source_moves`` are the source entries booked in the paying company.
        Each one has a mirror counterpart entry in the receiving company; the
        note reflects both sides so the payslip carries a complete picture.
        """
        self.ensure_one()
        rows = Markup('')
        for move in source_moves:
            currency = move.company_id.currency_id
            amount = sum(move.line_ids.mapped('debit'))
            rows += Markup('<li>%s → %s (%s): %s</li>') % (
                move.intercompany_source_company_id.name or move.company_id.name,
                move.intercompany_target_company_id.name or '',
                _('Draft'),
                formatLang(self.env, amount, currency_obj=currency),
            )
        body = (
            Markup('<p>%s</p>') % _(
                '%(count)d intercompany allocation(s) generated in Draft from '
                'this payslip (each with a source entry in the paying company '
                'and a counterpart entry in the receiving company). Review and '
                'post them manually.',
                count=len(source_moves),
            )
            + Markup('<ul>%s</ul>') % rows
        )
        self.message_post(body=body, subtype_xmlid='mail.mt_note')

    def _get_credit_account(self, company):
        account_id = int(self.env['ir.config_parameter'].sudo().get_param(
            'bt_hr_intercompany_salary.credit_account_%d' % company.id, 0
        ))
        if account_id:
            account = self.env['account.account'].browse(account_id).exists()
            if account:
                return account
        return self.env['account.account'].with_company(company).search([
            ('account_type', 'in', ['income', 'income_other']),
        ], limit=1)

    def _get_general_journal(self, company):
        journal_id = int(self.env['ir.config_parameter'].sudo().get_param(
            'bt_hr_intercompany_salary.payroll_journal_%d' % company.id, 0
        ))
        if journal_id:
            journal = self.env['account.journal'].browse(journal_id).exists()
            if journal:
                return journal.with_company(company)

        return self.env['account.journal'].with_company(company).search([
            ('company_id', '=', company.id),
            ('type', '=', 'general'),
        ], limit=1)

    def action_view_intercompany_invoices(self):
        self.ensure_one()
        moves = self.intercompany_invoice_ids
        action = self.env['ir.actions.act_window']._for_xml_id(
            'account.action_move_journal_line'
        )
        action['domain'] = [('id', 'in', moves.ids)]
        action['context'] = {'default_move_type': 'entry'}
        if len(moves) == 1:
            action['views'] = [(False, 'form')]
            action['res_id'] = moves.id
        return action

    def action_regenerate_intercompany_invoices(self):
        self.ensure_one()
        if self.state != 'validated':
            raise UserError(_('Payslip must be confirmed before generating journal entries.'))
        active_moves = self.intercompany_invoice_ids.filtered(
            lambda m: m.state != 'cancel'
        )
        if active_moves:
            raise UserError(_(
                'Active intercompany journal entries already exist. Cancel them first.'
            ))
        self._generate_intercompany_invoices()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Intercompany Journal Entries Generated'),
                'message': _('%d draft journal entry(s) created.', self.intercompany_invoice_count),
                'type': 'success',
                'sticky': False,
            }
        }
