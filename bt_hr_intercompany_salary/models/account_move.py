import logging

from markupsafe import Markup

from odoo import fields, models, api, _
from odoo.exceptions import UserError
from odoo.tools import formatLang, float_is_zero

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = 'account.move'

    intercompany_payslip_id = fields.Many2one(
        'hr.payslip',
        string='Source Payslip',
        readonly=True,
        copy=False,
        index=True,
        ondelete='set null',
        help='The payslip that triggered this intercompany journal entry.'
    )
    intercompany_source_company_id = fields.Many2one(
        'res.company',
        string='Source Company',
        readonly=True,
        copy=False,
        help='The parent company that issued this intercompany document.'
    )
    intercompany_target_company_id = fields.Many2one(
        'res.company',
        string='Target Company',
        readonly=True,
        copy=False,
        help='The company that is the recipient of this cost allocation.'
    )
    is_intercompany_salary_move = fields.Boolean(
        string='Intercompany Salary Journal Entry',
        copy=False,
    )
    intercompany_role = fields.Selection(
        selection=[
            ('source', 'Source (Receivable / Income)'),
            ('counterpart', 'Counterpart (Expense / Payable)'),
        ],
        string='Intercompany Role',
        readonly=True,
        copy=False,
        help='Indicates whether this entry is the source entry booked in the '
             'paying company or the counterpart entry booked in the receiving '
             'company.'
    )

    @api.model
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

    @api.model
    def _get_expense_account(self, company):
        account_id = int(self.env['ir.config_parameter'].sudo().get_param(
            'bt_hr_intercompany_salary.expense_account_%d' % company.id, 0
        ))
        if account_id:
            account = self.env['account.account'].browse(account_id).exists()
            if account:
                return account
        return self.env['account.account'].with_company(company).search([
            ('account_type', '=', 'expense'),
        ], limit=1)

    def _find_intercompany_counterpart(self):
        """Return an existing counterpart move for this source move, if any.

        Keeping this lookup in one place makes counterpart creation
        idempotent: regenerating or re-posting will never produce duplicate
        entries.
        """
        self.ensure_one()
        return self.sudo().search([
            ('intercompany_payslip_id', '=', self.intercompany_payslip_id.id),
            ('intercompany_source_company_id', '=', self.intercompany_source_company_id.id),
            ('intercompany_target_company_id', '=', self.intercompany_target_company_id.id),
            ('company_id', '=', self.intercompany_target_company_id.id),
            ('intercompany_role', '=', 'counterpart'),
            ('state', '!=', 'cancel'),
            ('id', '!=', self.id),
        ], limit=1)

    def _create_intercompany_counterpart_move(self, amount=None):
        """Create the draft counterpart entry in the target company.

        The source entry (booked in the paying company) debits an
        intercompany receivable and credits income. This method books the
        mirror image in the receiving company: it debits an expense account
        and credits the intercompany payable towards the paying company.

        The entry is always left in ``draft`` state so it can be reviewed
        before posting. Creation is idempotent - if a counterpart already
        exists it is returned untouched.

        :param float amount: allocation amount. When omitted it is derived
            from the source move's debit so the two entries always agree.
        :return: the counterpart ``account.move`` record (existing or new).
        """
        self.ensure_one()
        source_move = self

        target_company = source_move.intercompany_target_company_id
        source_company = source_move.intercompany_source_company_id
        payslip = source_move.intercompany_payslip_id

        if not target_company or not source_company or not payslip:
            _logger.warning(
                'Cannot create counterpart move: missing source company, '
                'target company or payslip for move %s.', source_move.name
            )
            return self.browse()

        existing = source_move._find_intercompany_counterpart()
        if existing:
            _logger.info(
                'Counterpart move %s already exists for source %s. Skipping.',
                existing.name, source_move.name
            )
            return existing

        if amount is None:
            amount = sum(source_move.line_ids.mapped('debit'))

        target_company_name = target_company.sudo().name

        if float_is_zero(amount, precision_digits=2):
            _logger.warning(
                'Allocation amount is zero for target company %s. '
                'Skipping counterpart move creation.', target_company_name
            )
            return self.browse()

        source_company_partner = source_company.sudo().partner_id.with_company(target_company)
        if not source_company_partner:
            raise UserError(_(
                'No partner found for source company %(source)s in target '
                'company %(target)s. Please ensure the source company has a '
                'partner record accessible by the target company.',
                source=source_company.name, target=target_company_name,
            ))

        payable_account = source_company_partner.property_account_payable_id
        expense_account = self.sudo()._get_expense_account(target_company)

        if not payable_account:
            raise UserError(_(
                'No payable account configured for partner %(partner)s in '
                'company %(company)s. Please set one on the partner record.',
                partner=source_company_partner.name, company=target_company_name,
            ))
        if not expense_account:
            raise UserError(_(
                'No expense account configured for company %(company)s. '
                'Please set one in Configuration -> Settings.',
                company=target_company_name,
            ))

        journal = self.sudo()._get_general_journal(target_company)
        if not journal:
            raise UserError(_(
                'No general journal found in company %(company)s.',
                company=target_company_name,
            ))

        narration = _(
            'Intercompany Salary Expense Allocation (Counterpart)\n'
            'Period: %(start)s to %(end)s\n'
            'Source Payslip: %(payslip_name)s',
            start=payslip.date_from,
            end=payslip.date_to,
            payslip_name=payslip.name,
        )

        counterpart_move_vals = {
            'move_type': 'entry',
            'company_id': target_company.id,
            'journal_id': journal.id,
            'date': source_move.date,
            'ref': 'SALARY-EXPENSE-%s' % (
                source_company.name[:10].upper().replace(' ', '_'),
            ),
            'narration': narration,
            'line_ids': [
                (0, 0, {
                    'name': _('Intercompany Expense - %s', source_company.name),
                    'account_id': expense_account.id,
                    'partner_id': source_company_partner.id,
                    'debit': amount,
                    'credit': 0.0,
                }),
                (0, 0, {
                    'name': _('Intercompany Payable - %s', source_company.name),
                    'account_id': payable_account.id,
                    'partner_id': source_company_partner.id,
                    'debit': 0.0,
                    'credit': amount,
                }),
            ],
            'intercompany_payslip_id': payslip.id,
            'intercompany_source_company_id': source_company.id,
            'intercompany_target_company_id': target_company.id,
            'is_intercompany_salary_move': True,
            'intercompany_role': 'counterpart',
        }

        with self.env.cr.savepoint():
            counterpart_move = self.env['account.move'].sudo().with_company(
                target_company
            ).create(counterpart_move_vals)
            _logger.info(
                'Created draft counterpart journal entry in %s for %s (%.2f).',
                target_company_name, source_company.name, amount
            )

        counterpart_move._log_intercompany_origin(source_move=source_move)
        return counterpart_move

    def _log_intercompany_origin(self, source_move=None):
        """Post a concise chatter note explaining how this entry was created."""
        for move in self:
            payslip = move.intercompany_payslip_id
            if not payslip:
                continue

            currency = move.company_id.currency_id
            amount = sum(move.line_ids.mapped('debit'))
            amount_label = formatLang(self.env, amount, currency_obj=currency)

            if move.intercompany_role == 'counterpart':
                heading = _('Generated as intercompany counterpart entry')
            else:
                heading = _('Generated as intercompany source entry')

            details = [
                (_('Source Payslip'), payslip.name or _('Draft Payslip')),
                (_('Employee'), payslip.employee_id.name or ''),
                (_('Payroll Period'), '%s -> %s' % (payslip.date_from, payslip.date_to)),
                (_('Paying Company'), move.intercompany_source_company_id.sudo().name or ''),
                (_('Receiving Company'), move.intercompany_target_company_id.sudo().name or ''),
                (_('Amount'), amount_label),
            ]
            rows = Markup('').join(
                Markup('<li><strong>%s:</strong> %s</li>') % (label, value)
                for label, value in details
            )
            body = (
                Markup('<p>%s</p>') % heading
                + Markup('<ul>%s</ul>') % rows
            )

            move.message_post(body=body, subtype_xmlid='mail.mt_note')
