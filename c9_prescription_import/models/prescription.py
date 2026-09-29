# -*- coding: utf-8 -*-
from odoo import models, fields, api


class C9Prescription(models.Model):
    _name = 'c9.prescription'
    _description = 'C9 Optical Prescription'
    _rec_name = 'name'
    _order = 'mdate desc, id desc'

    name = fields.Char(
        string='Reference',
        required=True,
        readonly=True,
        default=lambda self: self.env['ir.sequence'].next_by_code('c9.prescription'),
        copy=False,
    )

    # --- Customer mapping ---
    # CustomerId (source) <-> res.partner.c9_customer_id <-> partner_id
    partner_id = fields.Many2one(
        'res.partner',
        string='Customer',
        ondelete='restrict',
        index=True,
        help='Matched automatically from CustomerId against res.partner.c9_customer_id. '
             'Left empty when the CustomerId in the source file could not be matched.',
    )
    customer_id_raw = fields.Char(
        string='Customer ID',
        help='Original CustomerId value from the source file, exactly as provided '
             '(leading zeros and letters preserved, e.g. "0002", "CASH", "AA01").',
        index=True,
    )
    mapping_status = fields.Selection(
        [
            ('matched', 'Matched'),
            ('unmatched', 'Unmatched'),
        ],
        string='Mapping Status',
        default='unmatched',
        required=True,
        index=True,
        help='Matched: CustomerId was found on a res.partner (c9_customer_id) and partner_id is set. '
             'Unmatched: no customer with this CustomerId exists yet; partner_id is empty.',
    )

    # --- Existing Sales Order relationship (preserved / extendable) ---
    sale_order_id = fields.Many2one(
        'sale.order',
        string='Sales Order',
        ondelete='set null',
        index=True,
        help='Optional link to the Sales Order this prescription belongs to, '
             'when the prescription originates from / is used on an order.',
    )

    # --- Duplicate protection ---
    # Composite key over the fields that uniquely identify a source row:
    # Doctype + Docno + WH + CustomerId + mDate. Verified against the full
    # 204,208-row production file: this combination is 100% unique with
    # zero collisions, whereas Docno+Doctype alone collides constantly
    # (the same Docno/Doctype repeats across different warehouses).
    import_key = fields.Char(
        string='Import Key',
        index=True,
        copy=False,
        help='Internal deduplication key built from Doctype|Docno|WH|CustomerId|mDate. '
             'Prevents the same source row from being imported twice.',
    )

    # --- Source columns (all 49 fields) ---
    # Doctype/Docno/WH/CustomerId/mDate are structural (Char) - CustomerId lives in
    # customer_id_raw above. All optical/measurement columns are stored as Char
    # rather than Float: the source data mixes real decimals, blanks, and the
    # literal string "NULL" in tens of thousands of cells. Storing as Float would
    # silently coerce missing values to 0.0, which is not a safe substitute for
    # "no value entered" on a prescription record. Char preserves exactly what was
    # in the file; reporting/analytics can cast downstream where appropriate.
    doctype = fields.Char(string='Doctype', index=True)
    docno = fields.Char(string='Docno', index=True)
    wh = fields.Char(string='WH')
    rsph = fields.Char(string='RSPH')
    lsph = fields.Char(string='LSPH')
    rcyl = fields.Char(string='RCYL')
    lcyl = fields.Char(string='LCYL')
    raxis = fields.Char(string='RAXIS')
    laxis = fields.Char(string='LAXIS')
    radd = fields.Char(string='RADD')
    ladd = fields.Char(string='LADD')
    ripd = fields.Char(string='RIPD')
    lipd = fields.Char(string='LIPD')
    msync = fields.Char(string='mSync')
    rprism = fields.Char(string='RPRISM')
    lprism = fields.Char(string='LPRISM')
    mrprism = fields.Char(string='MRPRISM')
    mlprism = fields.Char(string='MLPRISM')
    rnct = fields.Char(string='RNCT')
    lnct = fields.Char(string='LNCT')
    rva = fields.Char(string='RVA')
    lva = fields.Char(string='LVA')
    rhgt = fields.Char(string='RHGT')
    lhgt = fields.Char(string='LHGT')
    reyeradius = fields.Char(string='REYERADIUS')
    leyeradius = fields.Char(string='LEYERADIUS')
    rclradius = fields.Char(string='RCLRADIUS')
    lclradius = fields.Char(string='LCLRADIUS')
    mdate = fields.Date(string='Date', index=True)
    mdate_raw = fields.Char(
        string='Date (Raw)',
        help='Original mDate text from the source file, kept in case it could not '
             'be parsed into a proper Date.',
    )
    rbase = fields.Char(string='RBASE')
    lbase = fields.Char(string='LBASE')
    motherspec = fields.Text(string='mOtherSpec')
    shape = fields.Char(string='Shape')
    frametype = fields.Char(string='FrameType')
    lenght = fields.Char(string='Lenght')
    height = fields.Char(string='Height')
    npd = fields.Char(string='NPD')
    thicknesstype = fields.Char(string='ThicknessType')
    thickness = fields.Char(string='Thickness')
    pdheightr = fields.Char(string='PDHeightR')
    pdheightl = fields.Char(string='PDHeightL')
    segmentheight = fields.Char(string='SegmentHeight')
    revision = fields.Char(string='REVISION')
    levision = fields.Char(string='LEVISION')
    colourdef = fields.Char(string='ColourDef')
    ocularmovment = fields.Char(string='OcularMovement')
    optlicenseno = fields.Char(string='OptLicenseNo')
    mreason = fields.Text(string='mReason')

    # tracking
    import_id = fields.Many2one(
        'c9.prescription.import',
        string='Import Batch',
        ondelete='set null',
        index=True,
    )

    _import_key_uniq = models.Constraint(
        'unique(import_key)',
        'This exact prescription row (same Doctype/Docno/WH/CustomerId/Date) has already been imported.'
    )
