from odoo import fields, api, models
from odoo.exceptions import UserError, ValidationError


class AddPower(models.Model):
    _name = 'power.add'
    _order = 'name'

    name = fields.Char()
    
