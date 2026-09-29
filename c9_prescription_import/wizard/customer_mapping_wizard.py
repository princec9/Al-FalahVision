# -*- coding: utf-8 -*-
import base64
import csv
import io
import logging
import re

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# Columns we need from the customer CSV
REQUIRED_CSV_COLUMNS = ['ACCODE', 'ACNAME', 'TELNO']


def _normalize_customer_id(raw):
    """Normalize a customer ID value to a clean Char string, preserving
    leading zeros.  Handles:
      "0002"    -> "0002"
      " 0002 "  -> "0002"
      "0002.0"  -> "0002"   (Excel sometimes adds .0 to text numbers)
      "CASH"    -> "CASH"
      ""        -> ""
    Never converts to int; always returns a string.
    """
    if raw is None:
        return ''
    text = str(raw).strip()
    if not text or text.upper() == 'NULL':
        return ''
    # Handle "0002.0" -> "0002" (float-like text where the decimal part is 0)
    m = re.match(r'^(\d+)\.0+$', text)
    if m:
        return m.group(1)
    return text


def _normalize_phone(phone):
    """Strip everything except digits from a phone number for comparison.
    Returns '' if the phone is empty or NULL."""
    if not phone:
        return ''
    text = str(phone).strip()
    if text.upper() == 'NULL':
        return ''
    return re.sub(r'\D', '', text)


def _normalize_name(name):
    """Lowercase, strip, collapse whitespace for name comparison."""
    if not name:
        return ''
    text = str(name).strip()
    if text.upper() == 'NULL':
        return ''
    return re.sub(r'\s+', ' ', text).strip().upper()


class C9CustomerMappingWizard(models.TransientModel):
    _name = 'c9.customer.mapping.wizard'
    _description = 'Map Customer CSV ACCODEs to Odoo Customers'

    import_file = fields.Binary(
        string='Customer CSV File',
        required=True,
        attachment=False,
    )
    filename = fields.Char(string='File Name')

    # --- Result fields (step 2: show results) ---
    state = fields.Selection([
        ('upload', 'Upload'),
        ('done', 'Done'),
    ], default='upload', required=True)

    total_customers = fields.Integer(string='Total Customers in CSV', readonly=True)
    matched_count = fields.Integer(string='Matched', readonly=True)
    unmatched_count = fields.Integer(string='Unmatched', readonly=True)
    ambiguous_count = fields.Integer(string='Ambiguous', readonly=True)
    conflict_count = fields.Integer(string='Conflicts', readonly=True)
    updated_count = fields.Integer(string='Updated (c9_customer_id set)', readonly=True)
    already_set_count = fields.Integer(string='Already Set', readonly=True)
    skipped_no_phone = fields.Integer(string='Skipped (No Phone)', readonly=True)

    result_details = fields.Text(string='Unmatched Details', readonly=True)
    ambiguous_details = fields.Text(string='Ambiguous Details', readonly=True)
    conflict_details = fields.Text(string='Conflict Details', readonly=True)

    # --- Remap prescriptions after mapping ---
    prescriptions_remapped = fields.Integer(string='Prescriptions Remapped', readonly=True)

    def _iter_csv_rows(self, raw):
        """Iterate over CSV rows, yielding dicts."""
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('latin-1')
        reader = csv.DictReader(io.StringIO(text))
        header = reader.fieldnames or []
        missing = [c for c in REQUIRED_CSV_COLUMNS if c not in header]
        if missing:
            raise UserError(_(
                'The CSV file is missing required column(s): %s\n\n'
                'Expected columns: ACCODE, ACNAME, TELNO'
            ) % ', '.join(missing))
        for row in reader:
            yield row

    def action_import(self):
        """Main entry point: read the customer CSV, match Name+Phone to Odoo
        res.partner records, and populate c9_customer_id."""
        self.ensure_one()
        if not self.import_file:
            raise UserError(_('Please upload the customer CSV file.'))

        fname = (self.filename or '').lower()
        raw = base64.b64decode(self.import_file)

        if not fname.endswith('.csv'):
            raise UserError(_(
                'Please upload a CSV file. '
                'Got: %s'
            ) % (self.filename or 'unknown'))

        _logger.info('Customer mapping started: file=%s', self.filename)

        # ------------------------------------------------------------------
        # STEP 1: Read CSV into a list of (ACCODE, ACNAME, TELNO)
        # ------------------------------------------------------------------
        csv_rows = []
        for row in self._iter_csv_rows(raw):
            accode = _normalize_customer_id(row.get('ACCODE', ''))
            acname = (row.get('ACNAME') or '').strip()
            telno = (row.get('TELNO') or '').strip()
            if not accode:
                continue
            # Skip NULL names
            if not acname or acname.upper() == 'NULL':
                continue
            csv_rows.append({
                'accode': accode,
                'acname': acname,
                'telno': telno,
            })

        total_customers = len(csv_rows)
        _logger.info('Customer CSV: %d valid rows', total_customers)

        # ------------------------------------------------------------------
        # STEP 2: Build Odoo partner lookup indexed by normalized (name, phone)
        # ------------------------------------------------------------------
        Partner = self.env['res.partner']
        # Read ALL partners with a name (individuals and companies)
        all_partners = Partner.search([('name', '!=', False)])
        _logger.info('Odoo partners loaded: %d', len(all_partners))

        # Build index: key = (normalized_name, phone_digits) -> list of partners
        partner_by_name_phone = {}
        for p in all_partners:
            norm_name = _normalize_name(p.name)
            if not norm_name:
                continue
            norm_phone = _normalize_phone(p.phone)
            if norm_phone:
                key = (norm_name, norm_phone)
                partner_by_name_phone.setdefault(key, []).append(p)

        # Also build an index: c9_customer_id -> partner for conflict detection
        existing_cid_map = {}
        for p in all_partners:
            cid = (p.c9_customer_id or '').strip()
            if cid:
                existing_cid_map.setdefault(cid, []).append(p)

        # ------------------------------------------------------------------
        # STEP 3: Match each CSV row
        # ------------------------------------------------------------------
        matched = 0
        unmatched = 0
        ambiguous = 0
        conflicts = 0
        updated = 0
        already_set = 0
        skipped_no_phone = 0

        unmatched_lines = []
        ambiguous_lines = []
        conflict_lines = []

        partners_to_update = {}  # partner_id -> accode

        for row in csv_rows:
            accode = row['accode']
            acname = row['acname']
            telno = row['telno']

            norm_name = _normalize_name(acname)
            norm_phone = _normalize_phone(telno)

            # Skip rows with no phone - cannot reliably match
            if not norm_phone:
                skipped_no_phone += 1
                unmatched += 1
                unmatched_lines.append(
                    '%s | %s | (no phone) | Cannot match without phone number'
                    % (accode, acname)
                )
                continue

            key = (norm_name, norm_phone)
            candidates = partner_by_name_phone.get(key, [])

            if len(candidates) == 0:
                unmatched += 1
                unmatched_lines.append(
                    '%s | %s | %s | No Odoo customer with this name and phone'
                    % (accode, acname, telno)
                )
                continue

            if len(candidates) > 1:
                ambiguous += 1
                partner_names = '; '.join(
                    '%s (ID %s, phone %s)' % (p.name, p.id, p.phone or '')
                    for p in candidates
                )
                ambiguous_lines.append(
                    '%s | %s | %s | Multiple matches: %s'
                    % (accode, acname, telno, partner_names)
                )
                continue

            # Exactly one match
            partner = candidates[0]

            # Check if this partner already has a DIFFERENT c9_customer_id
            existing_cid = (partner.c9_customer_id or '').strip()
            if existing_cid and existing_cid != accode:
                conflicts += 1
                conflict_lines.append(
                    '%s | %s | %s | Partner already has c9_customer_id=%s (partner ID %s)'
                    % (accode, acname, telno, existing_cid, partner.id)
                )
                continue

            # Check if another partner already has this ACCODE
            existing_holders = existing_cid_map.get(accode, [])
            other_holders = [p for p in existing_holders if p.id != partner.id]
            if other_holders:
                conflicts += 1
                holder_names = '; '.join(
                    '%s (ID %s)' % (p.name, p.id) for p in other_holders
                )
                conflict_lines.append(
                    '%s | %s | %s | ACCODE already assigned to: %s'
                    % (accode, acname, telno, holder_names)
                )
                continue

            if existing_cid == accode:
                # Already correctly set
                already_set += 1
                matched += 1
                continue

            # Record for update
            matched += 1
            partners_to_update[partner.id] = accode

        # ------------------------------------------------------------------
        # STEP 4: Write c9_customer_id to matched partners
        # ------------------------------------------------------------------
        items = list(partners_to_update.items())
        for start in range(0, len(items), 5000):
            batch = items[start:start + 5000]
            if not batch:
                continue
            self.env.cr.executemany(
                """
                UPDATE res_partner
                   SET c9_customer_id = %s,
                       write_uid = %s,
                       write_date = NOW()
                 WHERE id = %s
                   AND (c9_customer_id IS NULL OR c9_customer_id = '')
                """,
                [(accode, self.env.uid, partner_id) for partner_id, accode in batch],
            )
            updated += self.env.cr.rowcount
            self.env.cr.commit()
            _logger.info('Updated %d partners with c9_customer_id...', updated)
        if items:
            Partner.invalidate_model(['c9_customer_id'])
            Partner.browse([partner_id for partner_id, _accode in items]).modified(['c9_customer_id'])

        self.env.cr.commit()

        _logger.info(
            'Customer mapping completed: total=%s matched=%s unmatched=%s '
            'ambiguous=%s conflicts=%s updated=%s already_set=%s',
            total_customers, matched, unmatched, ambiguous, conflicts,
            updated, already_set,
        )

        # ------------------------------------------------------------------
        # STEP 5: Auto-remap unmatched prescriptions
        # ------------------------------------------------------------------
        prescriptions_remapped = self._remap_prescriptions()

        # ------------------------------------------------------------------
        # STEP 6: Show results
        # ------------------------------------------------------------------
        self.write({
            'state': 'done',
            'total_customers': total_customers,
            'matched_count': matched,
            'unmatched_count': unmatched,
            'ambiguous_count': ambiguous,
            'conflict_count': conflicts,
            'updated_count': updated,
            'already_set_count': already_set,
            'skipped_no_phone': skipped_no_phone,
            'result_details': '\n'.join(unmatched_lines[:500]) if unmatched_lines else False,
            'ambiguous_details': '\n'.join(ambiguous_lines[:500]) if ambiguous_lines else False,
            'conflict_details': '\n'.join(conflict_lines[:500]) if conflict_lines else False,
            'prescriptions_remapped': prescriptions_remapped,
        })

        return {
            'type': 'ir.actions.act_window',
            'name': _('Customer Mapping Result'),
            'res_model': self._name,
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'new',
            'context': {'form_view_ref': 'c9_prescription_import.view_c9_customer_mapping_wizard_result'},
        }

    def _remap_prescriptions(self):
        """After c9_customer_id values are populated, remap all unmatched
        prescriptions that have a customer_id_raw matching a partner's
        c9_customer_id."""
        Prescription = self.env['c9.prescription']
        Partner = self.env['res.partner']

        # Build fresh partner map
        partners_with_cid = Partner.search([('c9_customer_id', '!=', False)])
        cid_map = {}
        for p in partners_with_cid:
            cid = _normalize_customer_id(p.c9_customer_id)
            if cid:
                cid_map.setdefault(cid, p)

        # Find all unmatched prescriptions
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

        _logger.info('Prescriptions remapped: %d out of %d unmatched', remapped, len(unmatched))
        return remapped

    def action_remap_only(self):
        """Standalone action: remap prescriptions without re-uploading CSV.
        Called from the prescription import form or menu."""
        remapped = self._remap_prescriptions()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Prescription Remapping Complete'),
                'message': _('%d prescriptions remapped to matched customers.') % remapped,
                'sticky': False,
                'type': 'success',
            },
        }
