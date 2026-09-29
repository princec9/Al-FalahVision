# -*- coding: utf-8 -*-
import base64
import io

from odoo.tests.common import TransactionCase, tagged

CSV_HEADER = (
    "Doctype,Docno,WH,RSPH,LSPH,RCYL,LCYL,RAXIS,LAXIS,RADD,LADD,RIPD,LIPD,"
    "mSync,RPRISM,LPRISM,MRPRISM,MLPRISM,RNCT,LNCT,RVA,LVA,RHGT,LHGT,"
    "REYERADIUS,LEYERADIUS,RCLRADIUS,LCLRADIUS,CustomerId,mDate,RBASE,LBASE,"
    "mOtherSpec,Shape,FrameType,Lenght,Height,NPD,ThicknessType,Thickness,"
    "PDHeightR,PDHeightL,SegmentHeight,REVISION,LEVISION,ColourDef,"
    "OcularMovement,OptLicenseNo,mReason"
)


def _make_row(doctype='INV', docno='000001', wh='04', customer_id='0002',
              mdate='2024-09-01 00:00:00.000'):
    """Build one CSV data row with every non-key column left blank/NULL,
    matching the messy pattern found in the real source files."""
    fields = [doctype, docno, wh] + ['NULL'] * 25 + [customer_id, mdate] + ['NULL'] * 20
    # header has 49 columns total: 3 (doctype/docno/wh) + 25 + 2 (customerid/mdate) + 19 = 49
    fields = fields[:49]
    while len(fields) < 49:
        fields.append('NULL')
    return ','.join(fields)


def _csv_b64(rows):
    content = CSV_HEADER + '\n' + '\n'.join(rows) + '\n'
    return base64.b64encode(content.encode('utf-8'))


@tagged('post_install', '-at_install')
class TestC9PrescriptionImport(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Partner = self.env['res.partner']
        self.Prescription = self.env['c9.prescription']
        self.Wizard = self.env['c9.prescription.import.wizard']

    def _run_import(self, rows, filename='test.csv'):
        wizard = self.Wizard.create({
            'import_file': _csv_b64(rows),
            'filename': filename,
        })
        action = wizard.action_import()
        return self.env['c9.prescription.import'].browse(action['res_id'])

    # 1 & 2 & 3 & 4 --------------------------------------------------------
    def test_01_leading_zero_customer_id_matches_and_maps_partner(self):
        """Customer with c9_customer_id = '0002' + prescription CustomerId
        '0002' -> the created prescription must be matched and carry the
        right partner_id, with leading zeros preserved throughout."""
        partner = self.Partner.create({
            'name': 'Example Customer',
            'c9_customer_id': '0002',
        })
        import_rec = self._run_import([_make_row(customer_id='0002')])

        self.assertEqual(import_rec.matched_rows, 1)
        self.assertEqual(import_rec.unmatched_rows, 0)

        rx = import_rec.prescription_ids
        self.assertEqual(len(rx), 1)
        self.assertEqual(rx.partner_id, partner)
        self.assertEqual(rx.customer_id_raw, '0002')
        self.assertEqual(rx.mapping_status, 'matched')

    def test_05_unmatched_customer_id_is_imported_but_flagged(self):
        """A CustomerId with no matching partner must still be imported,
        with partner_id empty and mapping_status = 'unmatched' - never
        assigned to a random customer."""
        import_rec = self._run_import([_make_row(customer_id='9999')])

        self.assertEqual(import_rec.matched_rows, 0)
        self.assertEqual(import_rec.unmatched_rows, 1)

        rx = import_rec.prescription_ids
        self.assertEqual(len(rx), 1)
        self.assertFalse(rx.partner_id)
        self.assertEqual(rx.mapping_status, 'unmatched')
        self.assertIn('9999', import_rec.unmatched_details)

    def test_06_empty_customer_id_is_not_imported(self):
        """A row with data but no CustomerId at all must not silently
        create a prescription; it is counted as an error, not imported."""
        row = _make_row(customer_id='')
        import_rec = self._run_import([row])

        self.assertEqual(import_rec.error_rows, 1)
        self.assertEqual(import_rec.imported_rows, 0)
        self.assertEqual(len(import_rec.prescription_ids), 0)

    def test_07_duplicate_prescription_is_skipped_on_reimport(self):
        """Importing the exact same file twice must not create duplicate
        prescription records."""
        self.Partner.create({'name': 'Dup Customer', 'c9_customer_id': '0002'})
        row = _make_row(customer_id='0002')

        first = self._run_import([row])
        self.assertEqual(first.imported_rows, 1)

        second = self._run_import([row])
        self.assertEqual(second.imported_rows, 0)
        self.assertEqual(second.skipped_rows, 1)

        total_prescriptions = self.Prescription.search_count([
            ('customer_id_raw', '=', '0002'),
        ])
        self.assertEqual(total_prescriptions, 1)

    def test_08_multiple_prescriptions_same_customer(self):
        """The same customer can have several prescriptions, each a
        distinct record, all correctly linked to that one partner."""
        partner = self.Partner.create({'name': 'Repeat Customer', 'c9_customer_id': '0010'})
        rows = [
            _make_row(docno='000001', wh='01', customer_id='0010'),
            _make_row(docno='000002', wh='01', customer_id='0010'),
            _make_row(docno='000001', wh='02', customer_id='0010'),  # same docno, different WH
        ]
        import_rec = self._run_import(rows)

        self.assertEqual(import_rec.matched_rows, 3)
        self.assertEqual(len(partner.c9_prescription_ids), 3)
        self.assertEqual(partner.c9_prescription_count, 3)

    def test_09_customer_smart_button_action_domain(self):
        """The Prescriptions smart button on res.partner must open a list
        scoped to exactly that customer's prescriptions."""
        partner = self.Partner.create({'name': 'Smart Button Customer', 'c9_customer_id': '0077'})
        other = self.Partner.create({'name': 'Other Customer', 'c9_customer_id': '0088'})
        self._run_import([
            _make_row(docno='000001', customer_id='0077'),
            _make_row(docno='000002', customer_id='0088'),
        ])

        action = partner.action_view_c9_prescriptions()
        self.assertEqual(action['domain'], [('partner_id', '=', partner.id)])

        found = self.Prescription.search(action['domain'])
        self.assertEqual(len(found), 1)
        self.assertEqual(found.partner_id, partner)
        self.assertNotEqual(found.partner_id, other)

    def test_10_import_summary_counts(self):
        """The import history record must report accurate, consistent
        totals: total, imported (matched+unmatched), matched, unmatched,
        skipped and errors."""
        self.Partner.create({'name': 'Summary Customer', 'c9_customer_id': '0005'})
        rows = [
            _make_row(docno='000001', customer_id='0005'),   # matched
            _make_row(docno='000002', customer_id='0006'),   # unmatched
            _make_row(docno='000003', customer_id=''),       # error: empty CustomerId
        ]
        import_rec = self._run_import(rows)

        self.assertEqual(import_rec.total_rows, 3)
        self.assertEqual(import_rec.matched_rows, 1)
        self.assertEqual(import_rec.unmatched_rows, 1)
        self.assertEqual(import_rec.error_rows, 1)
        self.assertEqual(import_rec.imported_rows,
                          import_rec.matched_rows + import_rec.unmatched_rows)

    def test_blank_padding_rows_are_skipped_not_erred(self):
        """Fully blank rows (no Doctype, Docno or CustomerId at all) are
        padding, as found in the real source files, and must be skipped
        without being counted as errors or creating garbage records."""
        blank_row = ','.join(['NULL'] * 49)
        real_row = _make_row(customer_id='0002')
        self.Partner.create({'name': 'Padding Test', 'c9_customer_id': '0002'})

        import_rec = self._run_import([blank_row, blank_row, real_row])

        self.assertEqual(import_rec.total_rows, 3)
        self.assertEqual(import_rec.blank_rows, 2)
        self.assertEqual(import_rec.matched_rows, 1)
        self.assertEqual(import_rec.error_rows, 0)
