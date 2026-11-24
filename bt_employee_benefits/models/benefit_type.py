from odoo import models, fields, api, _

class BenefitType(models.Model):
    _name = 'benefit.type'
    _description = 'Benefit Type'

    name = fields.Char(string='Benefit Types', required=True, translate=True)
    active = fields.Boolean("Active", default=True)

    # General configuration
    time_type = fields.Selection([
        ('absence', 'Absence'),
        ('presence', 'Presence'),
    ], string='Count as', default='absence', required=True)

    notify_hr = fields.Selection([
        ('nobody', 'Nobody to notify'),
        ('manager', 'Notify Manager'),
    ], string='Notify HR', default='nobody')

    # Time Off Requests
    leave_validation_type = fields.Selection([
        ('no_validation', 'None needed'),
        ('manager', "By Employee's Approver"),
        ('hr', "By HR"),
    ], string='Approval', default='hr')

    allow_leave_encashment = fields.Boolean(
        string='Allow Leave Encashment', default=False
    )



    request_unit = fields.Selection([
        ('unit', 'Units')
    ], default='unit', string='Request Unit')

    # Allocation Requests
    requires_allocation = fields.Boolean(
        string='Requires allocation', default=True
    )
    # employee_requests = fields.Boolean(
    #     string='Allow Employee Requests', default=False
    # )
    allocation_validation_type = fields.Selection([
        ('no_validation', 'None needed'),
        ('manager', "By Employee's Approver"),
        ('hr', "By HR"),
    ], string='Approval', default='hr')

    # # Configuration
    # ignore_public_holidays = fields.Boolean(
    #     string='Ignore Public Holidays'
    # )
    # hide_on_dashboard = fields.Boolean(
    #     string='Hide On Dashboard'
    # )
    support_document = fields.Boolean(
        string='Require Supporting Document'
    )
    # accruals_eligible = fields.Boolean(
    #     string='Eligible for Accrual Rate'
    # )

    # # Negative Cap
    # allows_negative = fields.Boolean(
    #     string='Allow Negative Cap', default=False
    # )

    # # Display Option
    # color_name = fields.Char('Color in Report', help='This color will be used in the time off summary report')
    # cover_image = fields.Image('Cover Image')
