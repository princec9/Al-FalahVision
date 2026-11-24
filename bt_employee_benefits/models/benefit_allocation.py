from odoo import models, fields, api, _
from odoo.exceptions import ValidationError
from dateutil.relativedelta import relativedelta


class BenefitAllocation(models.Model):
    _name = 'benefit.allocation'
    _description = 'Benefit Allocation'
    _order = 'date_from desc'

    name = fields.Char(
        string='Description', compute='_compute_name',
        store=True, readonly=False
    )
    state = fields.Selection([
        ('draft', 'Draft'),
        ('confirm', 'Confirmed'),
        ('validate', 'Approved'),
        ('refuse', 'Refused'),
    ], default='draft', string='Status', tracking=True)

    employee_id = fields.Many2one(
        'hr.employee', string='Employee',
        required=True, tracking=True,
        default=lambda self: self.env.user.employee_id
    )
    department_id = fields.Many2one(
        'hr.department', string='Department',
        related='employee_id.department_id', store=True
    )
    holiday_status_id = fields.Many2one(
        'benefit.type', string='Benefit Type',
        required=True, tracking=True,
        domain=[('requires_allocation', '=', True)]
    )
    allocation_type = fields.Selection([
        ('regular', 'Regular Allocation'),
        ('accrual', 'Accrual Allocation'),
    ], default='regular', required=True, string='Allocation Type', tracking=True)

    accrual_plan_id = fields.Many2one(
        'benefit.accrual.plan', string='Accrual Plan',
        tracking=True
    )

    # Duration fields
    number_of_days = fields.Float(
        string='Number of Units', digits=(6, 2), default=0,
        tracking=True
    )
    number_of_hours = fields.Float(
        string='Number of Hours', digits=(6, 2), default=0
    )
    duration_display = fields.Char(
        string='Duration', compute='_compute_duration_display'
    )

    date_from = fields.Date(
        string='Start Date', default=fields.Date.today,
        required=True, tracking=True
    )
    date_to = fields.Date(string='Expiry Date', tracking=True)

    notes = fields.Html(string='Notes')
    approver_id = fields.Many2one(
        'hr.employee', string='Approval',
        default=lambda self: self.env.user.employee_id
    )
    lastcall = fields.Date(
        string='Last Accrual Date',
        default=fields.Date.today
    )
    nextcall = fields.Date(
        string='Next Accrual Date',
        default=fields.Date.today
    )

    @api.depends('employee_id', 'holiday_status_id', 'allocation_type')
    def _compute_name(self):
        for alloc in self:
            if alloc.employee_id and alloc.holiday_status_id:
                alloc.name = '%s - %s' % (
                    alloc.holiday_status_id.name,
                    alloc.employee_id.name
                )

    @api.depends('number_of_days', 'number_of_hours', 'holiday_status_id')
    def _compute_duration_display(self):
        for alloc in self:
            unit = alloc.holiday_status_id.request_unit if alloc.holiday_status_id else 'day'
            if unit == 'hour':
                alloc.duration_display = '%.2f Hours' % alloc.number_of_hours
            elif unit == 'unit':
                alloc.duration_display = '%.2f Units' % alloc.number_of_days
            else:
                alloc.duration_display = '%.2f Units' % alloc.number_of_days


    @api.onchange('allocation_type')
    def _onchange_allocation_type(self):
        if self.allocation_type == 'regular':
            self.accrual_plan_id = False

    @api.constrains('date_from', 'date_to')
    def _check_dates(self):
        for alloc in self:
            if alloc.date_to and alloc.date_from > alloc.date_to:
                raise ValidationError(
                    _('Start Date must be before Expiry Date.')
                )

    def action_confirm(self):
        self.write({'state': 'confirm'})

    def action_approve(self):
        self.write({'state': 'validate'})

    def action_refuse(self):
        self.write({'state': 'refuse'})

    def action_reset_draft(self):
        self.write({'state': 'draft'})

    @api.model
    def _update_accrual(self):
        """
        Scheduler action — runs daily.
        Loops through all accrual-type approved allocations
        and adds days based on the linked accrual plan milestones.
        """
        today = fields.Date.today()

        accrual_allocations = self.search([
            ('allocation_type', '=', 'accrual'),
            ('state', '=', 'validate'),
            ('accrual_plan_id', '!=', False),
            ('nextcall', '<=', today),
        ])

        import logging
        _logger = logging.getLogger(__name__)

        for allocation in accrual_allocations:
            plan = allocation.accrual_plan_id
            level = self._get_current_level(allocation, plan, today)
            if not level:
                continue
            days_to_add = self._compute_accrual_days(level)
            if level.added_value_type == 'hour':
                allocation.number_of_hours += days_to_add
            else:
                allocation.number_of_days += days_to_add
            
            allocation.lastcall = today
            allocation.nextcall = self._get_next_date(today, level.frequency)
            _logger.info("Accrued %.4f for Allocation %s. Lastcall: %s, Nextcall: %s", 
                         days_to_add, allocation.name, allocation.lastcall, allocation.nextcall)

    def _get_current_level(self, allocation, plan, today):
        """
        Returns the applicable milestone level
        based on time elapsed since allocation start.
        """
        if not plan.level_ids:
            return False

        date_start = allocation.date_from
        elapsed_days = (today - date_start).days

        applicable_level = False
        for level in plan.level_ids.sorted('start_count'):
            # Convert start_count to days based on start_type
            if level.milestone_reached == 'allocation':
                start_days = 0
            else:
                if level.start_type == 'day':
                    start_days = level.start_count
                elif level.start_type == 'month':
                    start_days = level.start_count * 30
                else:  # year
                    start_days = level.start_count * 365

            if elapsed_days >= start_days:
                applicable_level = level

        return applicable_level

    def _compute_accrual_days(self, level):
        """
        Returns days to add per accrual run based on frequency.
        """
        # The nextcall logic already ensures this runs at the correct intervals.
        # added_value should represent the amount to be added at each interval.
        days = level.added_value

        # Apply yearly cap if set
        if level.cap_accrued_time and level.maximum_leave > 0:
            days = min(days, level.maximum_leave)

        return round(days, 4)

    def _get_next_date(self, today, frequency):
        """
        Returns the next scheduled accrual date
        based on frequency.
        """
        freq_map = {
            'daily': relativedelta(days=1),
            'weekly': relativedelta(weeks=1),
            'twice_a_month': relativedelta(days=15),
            'monthly': relativedelta(months=1),
            'twice_a_year': relativedelta(months=6),
            'yearly': relativedelta(years=1),
        }
        return today + freq_map.get(frequency, relativedelta(days=1))



