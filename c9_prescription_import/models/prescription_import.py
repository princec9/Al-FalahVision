# -*- coding: utf-8 -*-
import logging
import re

from odoo import models, fields, _

_logger = logging.getLogger(__name__)


def _normalize_customer_id(raw):
    """Normalize a customer ID preserving leading zeros."""
    if raw is None:
        return ''
    text = str(raw).strip()
    if not text or text.upper() == 'NULL':
        return ''
    m = re.match(r'^(\d+)\.0+$', text)
    if m:
        return m.group(1)
    return text


class C9PrescriptionImport(models.Model):
    _name = 'c9.prescription.import'
    _description = 'C9 Prescription Import History'
    _rec_name = 'name'
    _order = 'create_date desc'

    name = fields.Char(
        string='Import Reference',
        required=True,
        readonly=True,
        default=lambda self: self.env['ir.sequence'].next_by_code('c9.prescription.import'),
        copy=False,
    )
    filename = fields.Char(string='File Name', readonly=True)
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('in_progress', 'In Progress'),
            ('done', 'Done'),
            ('failed', 'Failed'),
        ],
        string='Status',
        default='draft',
        required=True,
        readonly=True,
    )

    # --- Summary counters ---
    total_rows = fields.Integer(string='Total Rows', readonly=True,
                                 help='Every data row read from the source file, including blank/padding rows.')
    blank_rows = fields.Integer(string='Blank Rows Skipped', readonly=True,
                                 help='Rows with no Doctype, Docno or CustomerId at all - treated as padding, not imported.')
    imported_rows = fields.Integer(string='Imported', readonly=True,
                                    help='Prescriptions actually created (matched + unmatched).')
    matched_rows = fields.Integer(string='Matched', readonly=True,
                                   help='Imported prescriptions whose CustomerId was matched to a customer.')
    unmatched_rows = fields.Integer(string='Unmatched', readonly=True,
                                     help='Imported prescriptions whose CustomerId could not be matched to any customer.')
    skipped_rows = fields.Integer(string='Skipped (Duplicate)', readonly=True,
                                   help='Rows skipped because the same Doctype/Docno/WH/CustomerId/Date was already imported.')
    error_rows = fields.Integer(string='Errors', readonly=True,
                                 help='Rows that could not be imported at all (empty CustomerId, unreadable row, etc).')

    unmatched_details = fields.Text(
        string='Unmatched / Error Details',
        readonly=True,
        help='One line per unmatched or errored row, formatted as "CustomerId | Reason".',
    )

    prescription_ids = fields.One2many(
        'c9.prescription',
        'import_id',
        string='Prescriptions',
        readonly=True,
    )
    prescription_count = fields.Integer(
        string='Prescription Count',
        compute='_compute_prescription_count',
    )
    import_date = fields.Datetime(
        string='Import Date',
        readonly=True,
        default=fields.Datetime.now,
    )
    notes = fields.Text(string='Notes', readonly=True)

    def _compute_prescription_count(self):
        for rec in self:
            rec.prescription_count = len(rec.prescription_ids)

    def action_view_prescriptions(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'Prescriptions',
            'res_model': 'c9.prescription',
            'view_mode': 'list,form',
            'domain': [('import_id', '=', self.id)],
            'context': {},
        }

    def action_remap_customers(self):
        """Remap all unmatched prescriptions in this import batch (or globally
        if called without a specific record) using the current c9_customer_id
        values on res.partner."""
        Prescription = self.env['c9.prescription']
        Partner = self.env['res.partner']

        # Build fresh partner map from c9_customer_id
        partners_with_cid = Partner.search([('c9_customer_id', '!=', False)])
        cid_map = {}
        for p in partners_with_cid:
            cid = _normalize_customer_id(p.c9_customer_id)
            if cid:
                cid_map.setdefault(cid, p)

        # Find unmatched prescriptions (scoped to this batch or global)
        domain = [('mapping_status', '=', 'unmatched')]
        if self:
            domain.append(('import_id', 'in', self.ids))
        unmatched = Prescription.search(domain)
        remapped = 0

        for rx in unmatched:
            cid = _normalize_customer_id(rx.customer_id_raw)
            if not cid:
                continue
            partner = cid_map.get(cid)
            if partner:
                rx.write({
                    'partner_id': partner.id,
                    'mapping_status': 'matched',
                })
                remapped += 1

        # Update counters on the import record(s)
        for rec in self:
            new_matched = Prescription.search_count([
                ('import_id', '=', rec.id),
                ('mapping_status', '=', 'matched'),
            ])
            new_unmatched = Prescription.search_count([
                ('import_id', '=', rec.id),
                ('mapping_status', '=', 'unmatched'),
            ])
            rec.sudo().write({
                'matched_rows': new_matched,
                'unmatched_rows': new_unmatched,
            })

        _logger.info('Remap customers: %d prescriptions remapped', remapped)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Remap Complete'),
                'message': _('%d prescriptions remapped to matched customers.') % remapped,
                'sticky': False,
                'type': 'success',
            },
        }

    def action_remap_all_customers(self):
        """Remap ALL unmatched prescriptions globally, not scoped to any batch."""
        Prescription = self.env['c9.prescription']
        Partner = self.env['res.partner']

        partners_with_cid = Partner.search([('c9_customer_id', '!=', False)])
        cid_map = {}
        for p in partners_with_cid:
            cid = _normalize_customer_id(p.c9_customer_id)
            if cid:
                cid_map.setdefault(cid, p)

        unmatched = Prescription.search([('mapping_status', '=', 'unmatched')])
        remapped = 0

        for rx in unmatched:
            cid = _normalize_customer_id(rx.customer_id_raw)
            if not cid:
                continue
            partner = cid_map.get(cid)
            if partner:
                rx.write({
                    'partner_id': partner.id,
                    'mapping_status': 'matched',
                })
                remapped += 1

        # Update ALL import records that may have been affected
        affected_imports = Prescription.search([
            ('mapping_status', 'in', ['matched', 'unmatched']),
            ('import_id', '!=', False),
        ]).mapped('import_id')
        for rec in affected_imports:
            new_matched = Prescription.search_count([
                ('import_id', '=', rec.id),
                ('mapping_status', '=', 'matched'),
            ])
            new_unmatched = Prescription.search_count([
                ('import_id', '=', rec.id),
                ('mapping_status', '=', 'unmatched'),
            ])
            rec.sudo().write({
                'matched_rows': new_matched,
                'unmatched_rows': new_unmatched,
            })

        _logger.info('Global remap: %d prescriptions remapped', remapped)

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Global Remap Complete'),
                'message': _('%d prescriptions remapped to matched customers.') % remapped,
                'sticky': False,
                'type': 'success',
            },
        }
