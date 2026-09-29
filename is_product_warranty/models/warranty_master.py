from odoo import models, fields

# Support team configuration
class WarrantyTeam(models.Model):
    _name = 'warranty.team'
    _description = 'Warranty Support Team'
    
    name = fields.Char(string='Team Name', required=True)
    member_ids = fields.Many2many('res.users', string='Members')

# Tags for classification
class WarrantyTag(models.Model):
    _name = 'warranty.tag'
    _description = 'Warranty Tags'
    
    name = fields.Char(string='Tag', required=True)
    color = fields.Integer(string='Color')

# Categorization for claims
class WarrantyCategory(models.Model):
    _name = 'warranty.category'
    _description = 'Warranty Category'
    
    name = fields.Char(string='Category', required=True)

# Stages for pipeline management
class WarrantyStage(models.Model):
    _name = 'warranty.stage'
    _description = 'Warranty Stage'
    
    name = fields.Char(string='Stage', required=True)
    sequence = fields.Integer(default=1)
    