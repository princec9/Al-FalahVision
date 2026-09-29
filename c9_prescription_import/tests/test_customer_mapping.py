# -*- coding: utf-8 -*-
import base64

from odoo.tests.common import TransactionCase, tagged

CSV_HEADER = "ACCODE,ACNAME,TELNO,FAXNO,ChequeName,POBOX"


def _csv_b64(rows):
    content = CSV_HEADER + '\n' + '\n'.join(rows) + '\n'
    return base64.b64encode(content.encode('utf-8'))


@tagged('post_install', '-at_install')
class TestC9CustomerMapping(TransactionCase):

    def setUp(self):
        super().setUp()
        self.Partner = self.env['res.partner']
        self.Wizard = self.env['c9.customer.mapping.wizard']

    def _run_import(self, rows, filename='customers.csv'):
        wizard = self.Wizard.create({
            'import_file': _csv_b64(rows),
            'filename': filename,
        })
        wizard.action_import()
        return wizard

    def test_01_new_columns_are_mapped_to_partner_fields(self):
        """TELNO/FAXNO/ChequeName/POBOX from the Customer Master file must
        land on phone/fax/x_check_name/x_po_box of the single matched
        partner, without creating a second res.partner."""
        partner = self.Partner.create({
            'name': 'Acme Optics',
            'phone': '044123456',
        })
        wizard = self._run_import([
            '0002,Acme Optics,044123456,044999888,ACME CHEQUES LLC,PO BOX 12345',
        ])

        self.assertEqual(wizard.matched_count, 1)
        self.assertEqual(partner.c9_customer_id, '0002')
        self.assertEqual(partner.phone, '044123456')
        self.assertEqual(partner.fax, '044999888')
        self.assertEqual(partner.x_check_name, 'ACME CHEQUES LLC')
        self.assertEqual(partner.x_po_box, 'PO BOX 12345')

        self.assertEqual(
            self.Partner.search_count([('name', '=', 'Acme Optics')]), 1,
        )

    def test_02_blank_source_values_preserve_existing_data(self):
        """Re-importing with FAXNO/ChequeName/POBOX left blank/NULL must not
        wipe out values already stored on the partner."""
        partner = self.Partner.create({
            'name': 'Beta Optics',
            'phone': '044555000',
            'fax': '044555111',
            'x_check_name': 'BETA CHEQUES',
            'x_po_box': 'PO BOX 999',
            'c9_customer_id': '0003',
        })
        self._run_import([
            '0003,Beta Optics,044555000,NULL,,',
        ])

        self.assertEqual(partner.phone, '044555000')
        self.assertEqual(partner.fax, '044555111')
        self.assertEqual(partner.x_check_name, 'BETA CHEQUES')
        self.assertEqual(partner.x_po_box, 'PO BOX 999')

    def test_03_already_matched_partner_still_gets_fields_refreshed(self):
        """A partner whose c9_customer_id is already correctly set must
        still receive the phone/fax/check-name/PO-box refresh (not just
        brand-new matches)."""
        partner = self.Partner.create({
            'name': 'Gamma Optics',
            'phone': '044777000',
            'c9_customer_id': '0004',
        })
        wizard = self._run_import([
            '0004,Gamma Optics,044777000,044777999,GAMMA CHEQUES,PO BOX 42',
        ])

        self.assertEqual(wizard.already_set_count, 1)
        self.assertEqual(partner.fax, '044777999')
        self.assertEqual(partner.x_check_name, 'GAMMA CHEQUES')
        self.assertEqual(partner.x_po_box, 'PO BOX 42')

    def test_04_leading_zeros_in_phone_and_fax_are_preserved(self):
        """Phone/fax values must be stored exactly as provided, including
        leading zeros - never coerced to a number."""
        partner = self.Partner.create({
            'name': 'Delta Optics',
            'phone': '0501234567',
        })
        self._run_import([
            '0005,Delta Optics,0501234567,0501234000,Delta Cheques,PO BOX 007',
        ])

        self.assertEqual(partner.phone, '0501234567')
        self.assertEqual(partner.fax, '0501234000')
