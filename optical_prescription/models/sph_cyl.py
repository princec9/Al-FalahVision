
from odoo import fields, api, models, _
from odoo.exceptions import UserError, ValidationError

class SphCyl(models.Model):
    _name = 'sph.cyl'
    _order = 'name'

    name = fields.Char()
    
