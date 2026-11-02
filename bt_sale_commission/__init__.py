# -*- coding: utf-8 -*-
from . import models


def uninstall_hook(env):
    env['ir.actions.act_window']._bt_restore_native_commission_report()
