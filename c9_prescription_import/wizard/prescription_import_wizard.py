# -*- coding: utf-8 -*-
import base64
import csv
import io
import logging
from datetime import date, datetime

from odoo import models, fields, api, _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

BATCH_SIZE = 1500

# Source column header -> model field name. CustomerId is handled separately
# (it drives customer matching and is stored on customer_id_raw), and mDate is
# handled separately too (parsed into mdate / mdate_raw).
FIELD_MAP = {
    'Doctype': 'doctype',
    'Docno': 'docno',
    'WH': 'wh',
    'RSPH': 'rsph',
    'LSPH': 'lsph',
    'RCYL': 'rcyl',
    'LCYL': 'lcyl',
    'RAXIS': 'raxis',
    'LAXIS': 'laxis',
    'RADD': 'radd',
    'LADD': 'ladd',
    'RIPD': 'ripd',
    'LIPD': 'lipd',
    'mSync': 'msync',
    'RPRISM': 'rprism',
    'LPRISM': 'lprism',
    'MRPRISM': 'mrprism',
    'MLPRISM': 'mlprism',
    'RNCT': 'rnct',
    'LNCT': 'lnct',
    'RVA': 'rva',
    'LVA': 'lva',
    'RHGT': 'rhgt',
    'LHGT': 'lhgt',
    'REYERADIUS': 'reyeradius',
    'LEYERADIUS': 'leyeradius',
    'RCLRADIUS': 'rclradius',
    'LCLRADIUS': 'lclradius',
    'CustomerId': None,   # handled specially -> customer_id_raw + partner_id
    'mDate': None,        # handled specially -> mdate + mdate_raw
    'RBASE': 'rbase',
    'LBASE': 'lbase',
    'mOtherSpec': 'motherspec',
    'Shape': 'shape',
    'FrameType': 'frametype',
    'Lenght': 'lenght',
    'Height': 'height',
    'NPD': 'npd',
    'ThicknessType': 'thicknesstype',
    'Thickness': 'thickness',
    'PDHeightR': 'pdheightr',
    'PDHeightL': 'pdheightl',
    'SegmentHeight': 'segmentheight',
    'REVISION': 'revision',
    'LEVISION': 'levision',
    'ColourDef': 'colourdef',
    'OcularMovement': 'ocularmovment',
    'OptLicenseNo': 'optlicenseno',
    'mReason': 'mreason',
}

# Every expected source column, in file order - used to build the header index
# for XLSX (which has no built-in DictReader) and to sanity-check CSV headers.
EXPECTED_COLUMNS = [
    'Doctype', 'Docno', 'WH', 'RSPH', 'LSPH', 'RCYL', 'LCYL', 'RAXIS', 'LAXIS',
    'RADD', 'LADD', 'RIPD', 'LIPD', 'mSync', 'RPRISM', 'LPRISM', 'MRPRISM',
    'MLPRISM', 'RNCT', 'LNCT', 'RVA', 'LVA', 'RHGT', 'LHGT', 'REYERADIUS',
    'LEYERADIUS', 'RCLRADIUS', 'LCLRADIUS', 'CustomerId', 'mDate', 'RBASE',
    'LBASE', 'mOtherSpec', 'Shape', 'FrameType', 'Lenght', 'Height', 'NPD',
    'ThicknessType', 'Thickness', 'PDHeightR', 'PDHeightL', 'SegmentHeight',
    'REVISION', 'LEVISION', 'ColourDef', 'OcularMovement', 'OptLicenseNo',
    'mReason',
]

DATE_FORMATS = (
    '%Y-%m-%d %H:%M:%S.%f',
    '%Y-%m-%d %H:%M:%S',
    '%Y-%m-%d',
    '%d/%m/%Y %H:%M:%S',
    '%d/%m/%Y',
    '%m/%d/%Y',
    '%d-%m-%Y',
)


def _to_text(val):
    """Coerce any cell value (str/int/float/datetime/None) to a clean string,
    also collapsing the literal text "NULL" (very common in the source data)
    down to an empty string so it is never mistaken for a real value."""
    if val is None:
        return ''
    if isinstance(val, float) and val.is_integer():
        # openpyxl may hand back whole numbers as float (e.g. 2.0) for columns
        # that are not text-formatted; render them without a trailing .0.
        text = str(int(val))
    else:
        text = str(val)
    text = text.strip()
    if text.upper() == 'NULL':
        return ''
    return text


def _parse_date(val):
    """Return (date_value_or_False, raw_text). Accepts a string or a
    datetime/date object (openpyxl can hand back real datetime cells)."""
    if val is None or val == '':
        return False, ''
    if isinstance(val, datetime):
        return val.date(), val.isoformat()
    if isinstance(val, date):
        return val, val.isoformat()
    raw = str(val).strip()
    if not raw or raw.upper() == 'NULL':
        return False, ''
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date(), raw
        except ValueError:
            continue
    return False, raw


def _normalize_customer_id(cid):
    """Digits-only fallback form used only when an exact match fails, so that
    "0002" can still match a partner stored as "2" (per spec) without us
    ever *storing* the normalized form - the raw CustomerId is always kept."""
    if cid.isdigit():
        return str(int(cid))
    return None


class C9PrescriptionImportWizard(models.TransientModel):
    _name = 'c9.prescription.import.wizard'
    _description = 'Import Prescriptions from CSV or XLSX'

    import_file = fields.Binary(string='File (CSV or XLSX)', required=True, attachment=False)
    filename = fields.Char(string='File Name')

    # --- shared helpers -------------------------------------------------

    def _iter_csv_rows(self, raw):
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('latin-1')
        reader = csv.DictReader(io.StringIO(text))
        missing = [c for c in EXPECTED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise UserError(_(
                'The CSV file is missing expected column(s): %s'
            ) % ', '.join(missing))
        for row in reader:
            yield row

    def _iter_xlsx_rows(self, raw):
        try:
            import openpyxl
        except ImportError:
            raise UserError(_('The "openpyxl" Python library is required to import XLSX files.'))

        wb = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        rows_iter = ws.iter_rows(values_only=True)
        try:
            header = next(rows_iter)
        except StopIteration:
            wb.close()
            return
        header = [str(h).strip() if h is not None else '' for h in header]
        missing = [c for c in EXPECTED_COLUMNS if c not in header]
        if missing:
            wb.close()
            raise UserError(_(
                'The XLSX file is missing expected column(s): %s'
            ) % ', '.join(missing))
        col_index = {name: idx for idx, name in enumerate(header)}
        for values in rows_iter:
            row = {}
            for name, idx in col_index.items():
                row[name] = values[idx] if idx < len(values) else None
            yield row
        wb.close()

    def _build_partner_maps(self):
        """One single query for the whole run: {exact CustomerId -> partner}
        and a digit-normalized fallback map, per the batch-lookup requirement
        (never search per row)."""
        partners = self.env['res.partner'].search([('c9_customer_id', '!=', False)])
        exact_map = {}
        normalized_map = {}
        for p in partners:
            cid = (p.c9_customer_id or '').strip()
            if not cid:
                continue
            # First match wins on collision; real duplicate c9_customer_id
            # values on different partners are a data setup issue, not
            # something the importer should guess between.
            exact_map.setdefault(cid, p)
            norm = _normalize_customer_id(cid)
            if norm is not None:
                normalized_map.setdefault(norm, p)
        return exact_map, normalized_map

    def _match_partner(self, cid, exact_map, normalized_map):
        partner = exact_map.get(cid)
        if partner:
            return partner
        norm = _normalize_customer_id(cid)
        if norm is not None:
            return normalized_map.get(norm)
        return None

    # --- main entry point -------------------------------------------------

    def action_import(self):
        self.ensure_one()
        if not self.import_file:
            raise UserError(_('Please upload a CSV or XLSX file.'))

        fname = (self.filename or '').lower()
        raw = base64.b64decode(self.import_file)

        if fname.endswith('.csv'):
            row_iter = self._iter_csv_rows(raw)
        elif fname.endswith('.xlsx') or fname.endswith('.xlsm'):
            row_iter = self._iter_xlsx_rows(raw)
        else:
            raise UserError(_('Unsupported file type. Please upload a .csv or .xlsx file.'))

        _logger.info('Prescription import started: file=%s', self.filename)

        import_rec = self.env['c9.prescription.import'].create({
            'filename': self.filename or 'import',
            'state': 'in_progress',
            'import_date': fields.Datetime.now(),
        })

        Prescription = self.env['c9.prescription']
        exact_map, normalized_map = self._build_partner_maps()

        total = 0
        blank = 0
        matched = 0
        unmatched = 0
        skipped = 0
        errors = 0
        detail_lines = []

        batch = []

        def flush(rows):
            nonlocal matched, unmatched, skipped, errors

            # Build this batch's dedup keys and check which already exist in
            # a single query (never one search per row).
            parsed = []
            for row in rows:
                try:
                    parsed.append(self._parse_row(row))
                except Exception as exc:  # noqa: BLE001 - one bad row must not kill the batch
                    _logger.warning('Prescription import: unreadable row skipped (%s)', exc)
                    errors += 1
                    detail_lines.append('(unreadable row) | %s' % exc)

            keys = [p['import_key'] for p in parsed if p['import_key']]
            existing_keys = set()
            if keys:
                existing = Prescription.search_read(
                    [('import_key', 'in', keys)], ['import_key'],
                )
                existing_keys = {r['import_key'] for r in existing}

            to_create = []
            seen_in_batch = set()
            for p in parsed:
                cid = p['customer_id_raw']

                if not cid:
                    errors += 1
                    detail_lines.append('(empty) | Empty CustomerId')
                    continue

                key = p['import_key']
                if key in existing_keys or key in seen_in_batch:
                    skipped += 1
                    continue
                seen_in_batch.add(key)

                partner = self._match_partner(cid, exact_map, normalized_map)
                vals = p['vals']
                if partner:
                    vals['partner_id'] = partner.id
                    vals['mapping_status'] = 'matched'
                    matched += 1
                    _logger.info('CustomerId %s matched partner %s', cid, partner.id)
                else:
                    vals['partner_id'] = False
                    vals['mapping_status'] = 'unmatched'
                    unmatched += 1
                    detail_lines.append('%s | Customer not found' % cid)
                    _logger.info('CustomerId %s not found', cid)

                vals['import_id'] = import_rec.id
                to_create.append(vals)

            if to_create:
                Prescription.create(to_create)

        for row in row_iter:
            total += 1
            if self._is_blank_row(row):
                blank += 1
                continue
            batch.append(row)
            if len(batch) >= BATCH_SIZE:
                flush(batch)
                batch = []
                self.env.cr.commit()

        if batch:
            flush(batch)

        import_rec.write({
            'state': 'done',
            'total_rows': total,
            'blank_rows': blank,
            'imported_rows': matched + unmatched,
            'matched_rows': matched,
            'unmatched_rows': unmatched,
            'skipped_rows': skipped,
            'error_rows': errors,
            'unmatched_details': '\n'.join(detail_lines) if detail_lines else False,
        })

        _logger.info(
            'Prescription import completed: total=%s imported=%s matched=%s unmatched=%s '
            'skipped=%s errors=%s',
            total, matched + unmatched, matched, unmatched, skipped, errors,
        )

        return {
            'type': 'ir.actions.act_window',
            'name': _('Import Result'),
            'res_model': 'c9.prescription.import',
            'view_mode': 'form',
            'res_id': import_rec.id,
            'target': 'current',
        }

    # --- row-level helpers -------------------------------------------------

    def _is_blank_row(self, row):
        """A row is padding (not real data) only when it has neither a
        Doctype/Docno nor a CustomerId - matches the pattern found in the
        sample test file (1001 fully-empty rows out of 1024)."""
        doctype = _to_text(row.get('Doctype'))
        docno = _to_text(row.get('Docno'))
        cid = _to_text(row.get('CustomerId'))
        return not doctype and not docno and not cid

    def _parse_row(self, row):
        cid = _to_text(row.get('CustomerId'))
        doctype = _to_text(row.get('Doctype'))
        docno = _to_text(row.get('Docno'))
        wh = _to_text(row.get('WH'))
        mdate_val, mdate_raw = _parse_date(row.get('mDate'))

        import_key = '|'.join([doctype, docno, wh, cid, mdate_raw]) if cid else ''

        vals = {
            'customer_id_raw': cid,
            'doctype': doctype,
            'docno': docno,
            'wh': wh,
            'mdate': mdate_val,
            'mdate_raw': mdate_raw,
            'import_key': import_key,
        }
        for col, field_name in FIELD_MAP.items():
            if field_name is None:
                continue
            vals[field_name] = _to_text(row.get(col))

        return {
            'vals': vals,
            'customer_id_raw': cid,
            'import_key': import_key,
        }
