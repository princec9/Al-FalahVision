from odoo import models, fields, api, _
from odoo.exceptions import ValidationError, UserError


class BenefitRequest(models.Model):
    _name = 'benefit.request'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Benefit Request'
    _order = 'date_from desc'

    name = fields.Char(
        string='Reference', compute='_compute_name',
        store=True, readonly=False, copy=False,
    )

    state = fields.Selection([
        ('draft',    'To Approve'),
        ('validate', 'Approved'),
        ('refuse',   'Refused'),
    ], default='draft', string='Status', tracking=True)

    # Employee
    employee_id = fields.Many2one(
        'hr.employee', string='Employee', required=True,
        default=lambda self: self.env.user.employee_id,
        tracking=True
    )
    department_id = fields.Many2one(
        'hr.department', string='Department',
        related='employee_id.department_id', store=True
    )

    # Benefit Type
    holiday_status_id = fields.Many2one(
        'benefit.type', string='Benefit Type',
        required=True, tracking=True,
        domain="[('id', 'in', allowed_benefit_type_ids)]"
    )
    allowed_benefit_type_ids = fields.Many2many(
        'benefit.type', compute='_compute_allowed_benefit_types'
    )
    request_unit = fields.Selection(
        related='holiday_status_id.request_unit',
        string='Duration Type', readonly=True, store=True
    )

    # Allocation link
    allocation_id = fields.Many2one(
        'benefit.allocation', string='Allocation',
        domain="[('employee_id','=',employee_id),"
               "('holiday_status_id','=',holiday_status_id),"
               "('state','=','validate')]",
        tracking=True
    )
    allocation_remaining = fields.Float(
        string='Remaining Balance',
        compute='_compute_allocation_remaining',
        digits=(6, 2)
    )

    # Dates
    date_from = fields.Date(
        string='Date From', required=True,
        default=fields.Date.today, tracking=True
    )
    date_to = fields.Date(
        string='Date To', required=False, tracking=True
    )
    number_of_days = fields.Float(
        string='Duration', digits=(6, 2),
        default=0.0, readonly=False, store=True
    )
    number_of_hours = fields.Float(
        string='Number of Hours', digits=(6, 2)
    )
    number_of_units = fields.Float(
        string='Number of Units', digits=(6, 2)
    )
    duration_display = fields.Char(
        string='Duration', compute='_compute_duration_display'
    )

    # Description
    description = fields.Text(
        string='Description',
        placeholder='No description provided'
    )

    # Approver
    approver_id = fields.Many2one(
        'hr.employee', string='Approved By',
        readonly=True, tracking=True
    )
    refuse_reason = fields.Text(
        string='Refuse Reason', tracking=True
    )

    # ── Supporting Document ────────────────────────────────────

    # Mirrors the flag from the selected benefit type so the view
    # can show/hide the attachment widget without a custom JS widget.
    support_document = fields.Boolean(
        string='Requires Supporting Document',
        related='holiday_status_id.support_document',
        store=True, readonly=True,
    )

    # Stores the actual uploaded files via Odoo's standard
    # ir.attachment mechanism.  Using 'object' widget in the view
    # (widget="many2many_binary") gives the user a proper file picker.
    attachment_ids = fields.Many2many(
        'ir.attachment',
        'benefit_request_attachment_rel',   # explicit relation table name
        'request_id',
        'attachment_id',
        string='Supporting Documents',
        copy=False,
    )

    # ── Compute ────────────────────────────────────────────────

    @api.depends('employee_id')
    def _compute_allowed_benefit_types(self):
        for rec in self:
            if rec.employee_id:
                allocations = self.env['benefit.allocation'].search([
                    ('employee_id', '=', rec.employee_id.id),
                    ('state', '=', 'validate'),
                ])
                allocated_types = allocations.mapped('holiday_status_id')

                free_types = self.env['benefit.type'].search([
                    ('requires_allocation', '=', False)
                ])
                rec.allowed_benefit_type_ids = allocated_types | free_types
            else:
                rec.allowed_benefit_type_ids = self.env['benefit.type'].search([])

    @api.depends('employee_id', 'holiday_status_id',
                 'date_from', 'number_of_days', 'number_of_hours',
                 'number_of_units', 'request_unit')
    def _compute_name(self):
        for rec in self:
            if rec.employee_id and rec.holiday_status_id and rec.date_from:
                date_str = rec.date_from.strftime('%m/%d/%Y')

                unit_type = rec.request_unit
                if unit_type == 'hour':
                    duration = rec.number_of_hours
                    unit = 'hours'
                elif unit_type == 'unit':
                    duration = rec.number_of_units
                    unit = 'units'
                else:
                    duration = rec.number_of_days
                    unit = 'units'

                rec.name = '%s on %s: %g %s (%s)' % (
                    rec.employee_id.name,
                    rec.holiday_status_id.name,
                    duration,
                    unit,
                    date_str
                )
            elif rec.employee_id and rec.holiday_status_id:
                rec.name = '%s on %s' % (
                    rec.employee_id.name,
                    rec.holiday_status_id.name,
                )
            elif rec.holiday_status_id:
                rec.name = rec.holiday_status_id.name
            else:
                rec.name = 'New'

    @api.depends('number_of_days', 'number_of_hours',
                 'number_of_units', 'request_unit')
    def _compute_duration_display(self):
        for rec in self:
            unit = rec.request_unit
            if unit == 'hour':
                rec.duration_display = '%.2f Hours' % rec.number_of_hours
            elif unit == 'unit':
                rec.duration_display = '%.2f Units' % rec.number_of_units
            else:
                rec.duration_display = '( %g units )' % rec.number_of_days

    @api.depends('allocation_id', 'number_of_days',
                 'number_of_hours', 'number_of_units', 'state')
    def _compute_allocation_remaining(self):
        for rec in self:
            if not rec.allocation_id:
                rec.allocation_remaining = 0.0
                continue

            alloc = rec.allocation_id
            unit = rec.request_unit

            other_approved = self.env['benefit.request'].search([
                ('allocation_id', '=', alloc.id),
                ('state', '=', 'validate'),
                ('id', '!=', rec._origin.id or rec.id),
            ])

            if unit == 'hour':
                used = sum(other_approved.mapped('number_of_hours'))
                current = rec.number_of_hours
                total = alloc.number_of_hours
            elif unit == 'unit':
                used = sum(other_approved.mapped('number_of_units'))
                current = rec.number_of_units
                total = alloc.number_of_days
            else:
                used = sum(other_approved.mapped('number_of_days'))
                current = rec.number_of_days
                total = alloc.number_of_days

            rec.allocation_remaining = total - used - current

    # ── Onchange ───────────────────────────────────────────────

    @api.onchange('employee_id', 'holiday_status_id')
    def _onchange_employee_benefit(self):
        self.allocation_id = False
        if self.employee_id and self.holiday_status_id:
            allocation = self.env['benefit.allocation'].search([
                ('employee_id', '=', self.employee_id.id),
                ('holiday_status_id', '=', self.holiday_status_id.id),
                ('state', '=', 'validate'),
            ], limit=1)
            self.allocation_id = allocation

    @api.onchange('employee_id')
    def _onchange_employee_id(self):
        if self.employee_id:
            allocations = self.env['benefit.allocation'].search([
                ('employee_id', '=', self.employee_id.id),
                ('state', '=', 'validate'),
            ])
            allocated_types = allocations.mapped('holiday_status_id')

            free_types = self.env['benefit.type'].search([
                ('requires_allocation', '=', False)
            ])

            valid_types = allocated_types | free_types
            return {'domain': {'holiday_status_id': [('id', 'in', valid_types.ids)]}}
        return {'domain': {'holiday_status_id': []}}

    # ── Constraints ────────────────────────────────────────────

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for rec in self:
            if rec.date_from and rec.date_to:
                if rec.date_from > rec.date_to:
                    raise ValidationError(
                        _('Start Date must be before End Date.'))

    @api.constrains('number_of_days', 'number_of_hours',
                    'number_of_units', 'allocation_id')
    def _check_allocation_balance(self):
        for rec in self:
            if not rec.allocation_id:
                continue

            alloc = rec.allocation_id
            unit = rec.request_unit

            other_approved = self.env['benefit.request'].search([
                ('allocation_id', '=', alloc.id),
                ('state', '=', 'validate'),
                ('id', '!=', rec._origin.id or rec.id),
            ])

            if unit == 'hour':
                used = sum(other_approved.mapped('number_of_hours'))
                available = alloc.number_of_hours - used
                requested = rec.number_of_hours
            elif unit == 'unit':
                used = sum(other_approved.mapped('number_of_units'))
                available = alloc.number_of_days - used
                requested = rec.number_of_units
            else:
                used = sum(other_approved.mapped('number_of_days'))
                available = alloc.number_of_days - used
                requested = rec.number_of_days

            if requested > available:
                raise ValidationError(_(
                    'Requested (%.2f) exceeds available balance '
                    '(%.2f) after accounting for existing approved requests.'
                ) % (requested, available))

    @api.constrains('attachment_ids', 'holiday_status_id')
    def _check_supporting_document(self):
        """
        Blocks saving/approving when the selected benefit type requires
        a supporting document but none has been attached.
        """
        for rec in self:
            if (
                rec.holiday_status_id
                and rec.holiday_status_id.support_document
                and not rec.attachment_ids
            ):
                raise ValidationError(_(
                    'The benefit type "%s" requires a supporting document. '
                    'Please attach at least one file before saving.'
                ) % rec.holiday_status_id.name)

    # ── Actions ────────────────────────────────────────────────

    def action_validate(self):
        """
        Validate the request.  Re-checks the document constraint here
        so approval is blocked even if the record was somehow saved
        without triggering the ORM constraint (e.g. direct RPC calls).
        """
        for rec in self:
            if (
                rec.holiday_status_id
                and rec.holiday_status_id.support_document
                and not rec.attachment_ids
            ):
                raise UserError(_(
                    'Cannot approve "%s": a supporting document is required '
                    'for benefit type "%s".'
                ) % (rec.name, rec.holiday_status_id.name))

            rec.write({
                'state': 'validate',
                'approver_id': self.env.user.employee_id.id,
            })
            template = self.env.ref('bt_employee_benefits.mail_template_benefit_request_approved', raise_if_not_found=False)
            if template and rec.employee_id.user_id:
                template.send_mail(rec.id, force_send=True)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        template = self.env.ref('bt_employee_benefits.mail_template_benefit_request_submitted',
                                raise_if_not_found=False)
        for rec in records:
            approver_user = False
            if rec.employee_id.benefit_manager_id:
                approver_user = rec.employee_id.benefit_manager_id
            elif rec.employee_id.parent_id and rec.employee_id.parent_id.user_id:
                approver_user = rec.employee_id.parent_id.user_id

            if template and approver_user:
                template.send_mail(rec.id, force_send=True)
        return records

    def action_refuse(self):
        for rec in self:
            rec.write({'state': 'refuse'})
            template = self.env.ref('bt_employee_benefits.mail_template_benefit_request_refused',
                                    raise_if_not_found=False)
            if template and rec.employee_id.user_id:
                template.send_mail(rec.id, force_send=True)

    def action_reset_draft(self):
        self.write({'state': 'draft', 'approver_id': False})