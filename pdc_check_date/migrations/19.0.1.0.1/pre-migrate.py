def migrate(cr, version):
    """check_date -> cheque_date rename.

    1. Keep existing data by renaming the column.
    2. Delete the views of the previous version. They still reference the
       removed `check_date` field and make the validation of the new views fail
       ("Field `check_date` does not exist"), because obsolete records are only
       removed at the very end of the module update.
    """
    cr.execute("""
        SELECT column_name FROM information_schema.columns
         WHERE table_name = 'account_payment'
           AND column_name IN ('check_date', 'cheque_date')
    """)
    cols = {row[0] for row in cr.fetchall()}
    if 'check_date' in cols and 'cheque_date' not in cols:
        cr.execute("ALTER TABLE account_payment RENAME COLUMN check_date TO cheque_date")

    cr.execute("""
        SELECT res_id FROM ir_model_data
         WHERE module = 'pdc_check_date'
           AND model = 'ir.ui.view'
           AND name LIKE %s
    """, [r'%\_check\_date'])
    view_ids = [row[0] for row in cr.fetchall()]
    if view_ids:
        cr.execute("DELETE FROM ir_ui_view WHERE id IN %s", [tuple(view_ids)])
        cr.execute("DELETE FROM ir_model_data WHERE model = 'ir.ui.view' AND res_id IN %s",
                   [tuple(view_ids)])
