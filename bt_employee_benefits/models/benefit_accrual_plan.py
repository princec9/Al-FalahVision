from odoo import models, fields, api


class BenefitAccrualPlan(models.Model):
    _name = 'benefit.accrual.plan'
    _description = 'Benefit Accrual Plan'
    _order = 'name'

    name = fields.Char(string='Title', required=True)
    active = fields.Boolean(default=True)

    accrued_gain_time = fields.Selection([
        ('start', 'At the start of the accrual period'),
        ('end',   'At the end of the accrual period'),
    ], string='When the time is accrued?',
        default='end', required=True
    )
    is_based_on_worked_time = fields.Selection([
        ('yes', 'Yes, consider the worked hours, excluding any time off taken during that period.'),
        ('no',  'No, always consider the entire accrual period (whole calendar days).'),
    ], string='It is based on worked time?',
        default='no', required=True
    )
    carryover_date = fields.Boolean(
        string='Do you need a carry-over of the accrued days from one year to another?',
        default=False
    )
    carryover_type = fields.Selection([
        ('year',       'At the start of the year'),
        ('allocation', 'At the allocation date'),
        ('other',      'Custom date : the 1'),
    ], string='When do you want the accrued time left to be carried-over?',
        default='year'
    )
    carryover_day = fields.Integer(
        string='Carry-Over Day', default=1
    )
    carryover_month = fields.Selection([
        ('1', 'January'), ('2', 'February'),  ('3', 'March'),
        ('4', 'April'),   ('5', 'May'),        ('6', 'June'),
        ('7', 'July'),    ('8', 'August'),     ('9', 'September'),
        ('10', 'October'),('11', 'November'), ('12', 'December'),
    ], string='Carry-Over Month', default='1'
    )

    level_ids = fields.One2many(
        'benefit.accrual.plan.level', 'plan_id',
        string='Milestones', copy=True
    )
    level_count = fields.Integer(
        string='Milestones', compute='_compute_level_count'
    )
    allocation_ids = fields.One2many(
        'benefit.allocation', 'accrual_plan_id',
        string='Allocations'
    )
    allocation_count = fields.Integer(
        string='Allocations', compute='_compute_allocation_count'
    )

    @api.depends('level_ids')
    def _compute_level_count(self):
        for plan in self:
            plan.level_count = len(plan.level_ids)

    @api.depends('allocation_ids')
    def _compute_allocation_count(self):
        for plan in self:
            plan.allocation_count = len(plan.allocation_ids)

    def action_open_allocations(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Allocations',
            'res_model': 'benefit.allocation',
            'view_mode': 'list,form',
            'domain': [('accrual_plan_id', '=', self.id)],
            'context': {
                'default_accrual_plan_id': self.id,
                'default_allocation_type': 'accrual',
            },
        }


class BenefitAccrualPlanLevel(models.Model):
    _name = 'benefit.accrual.plan.level'
    _description = 'Benefit Accrual Plan Milestone'
    _order = 'start_count'

    plan_id = fields.Many2one(
        'benefit.accrual.plan', string='Accrual Plan',
        ondelete='cascade', required=True
    )

    # ── ACCRUAL LEVEL OPTIONS ─────────────────────────────────

    # Frequency: "1 Day(s) Daily"
    added_value = fields.Float(
        string='Rate', digits=(6, 2),
        default=1.0, required=True
    )
    added_value_type = fields.Selection([
        ('day',  'Day(s)'),
        ('hour', 'Hour(s)'),
        ('unit', 'Unit(s)'),
    ], default='day', required=True,
        string='Rate Unit'
    )
    frequency = fields.Selection([
        ('daily',         'Daily'),
        ('weekly',        'Weekly'),
        ('twice_a_month', 'Twice a month'),
        ('monthly',       'Monthly'),
        ('twice_a_year',  'Twice a year'),
        ('yearly',        'Yearly'),
    ], default='daily', required=True,
        string='Frequency'
    )

    # Milestone reached
    milestone_reached = fields.Selection([
        ('allocation', 'At allocation creation'),
        ('after',      'After'),
    ], default='allocation', required=True,
        string='This milestone will be reached'
    )
    start_count = fields.Integer(
        string='After', default=0
    )
    start_type = fields.Selection([
        ('day',   'Days'),
        ('month', 'Months'),
        ('year',  'Years'),
    ], default='day', required=True,
        string='Period'
    )

    # ── CARRY OVER OPTIONS ────────────────────────────────────

    action_with_unused_accruals = fields.Selection([
        ('lost',          'Lost'),
        ('carried_over',  'Carried over'),
    ], default='lost', required=True,
        string='After a year, unused time off will be'
    )

    # ── CAP OPTIONS ───────────────────────────────────────────

    cap_accrued_time = fields.Boolean(
        string='Define a yearly cap?', default=False
    )
    maximum_leave = fields.Float(
        string='Yearly Cap', digits=(6, 2), default=0
    )
    cap_accrued_balance = fields.Boolean(
        string='Define a balance cap?', default=False
    )
    maximum_balance = fields.Float(
        string='Balance Cap', digits=(6, 2), default=0
    )