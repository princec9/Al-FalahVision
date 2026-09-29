
from odoo import fields, models


class PrescriptionBy(models.Model):
    _name = "prescription.by"

    name = fields.Char("Name")


class FittingType(models.Model):
    _name = 'fitting.type'

    name = fields.Char()
