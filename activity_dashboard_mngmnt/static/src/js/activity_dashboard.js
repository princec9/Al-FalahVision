/** @odoo-module **/
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { Layout } from "@web/search/layout";
import { Component, onWillStart } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";

export class ActivityDashboard extends Component {
    setup() {
        this.orm = useService('orm');
        this.action = useService('action');
        onWillStart(async () => await this.render_dashboards());
    }

    async render_dashboards() {
        /** Method for rendering dashboard. */
        const [
            planned_activity,
            today_activity,
            overdue_activity,
            done_activity,
            activity_type,
        ] = await Promise.all([
            this.orm.call('mail.activity', 'search_read', [], {
                domain: [["state", "=", "planned"]],
            }),
            this.orm.call('mail.activity', 'search_read', [], {
                domain: [["state", "=", "today"]],
            }),
            this.orm.call('mail.activity', 'search_read', [], {
                domain: [["state", "=", "overdue"]],
            }),
            this.orm.call('mail.activity', 'search_read', [], {
                domain: [["state", "=", "done"], ["active", "in", [true, false]]],
            }),
            this.orm.call('mail.activity.type', 'search_count', [], { domain: [] }),
        ]);

        this.len_all = planned_activity.length + done_activity.length
            + today_activity.length + overdue_activity.length;
        this.len_planned = planned_activity.length;
        this.len_done = done_activity.length;
        this.len_today = today_activity.length;
        this.len_overdue = overdue_activity.length;
        this.done_activity = done_activity;
        this.planned_activity = planned_activity;
        this.today_activity = today_activity;
        this.overdue_activity = overdue_activity;
        this.activity_type = activity_type;
    }

    show_all_activities(e) {
        e.stopPropagation();
        e.preventDefault();
        this.action.doAction({
            name: _t("All Activities"),
            type: 'ir.actions.act_window',
            res_model: 'mail.activity',
            view_mode: 'list,form',
            domain: [['active', 'in', [true, false]]],
            views: [[false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    show_planned_activities(e) {
        e.stopPropagation();
        e.preventDefault();
        this.action.doAction({
            name: _t("Planned Activities"),
            type: 'ir.actions.act_window',
            res_model: 'mail.activity',
            domain: [['state', '=', 'planned']],
            view_mode: 'list,form',
            views: [[false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    show_completed_activities(e) {
        e.stopPropagation();
        e.preventDefault();
        this.action.doAction({
            name: _t("Completed Activities"),
            type: 'ir.actions.act_window',
            res_model: 'mail.activity',
            domain: [['state', '=', 'done'], ['active', 'in', [true, false]]],
            view_mode: 'list,form',
            views: [[false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    show_today_activities(e) {
        e.stopPropagation();
        e.preventDefault();
        this.action.doAction({
            name: _t("Today's Activities"),
            type: 'ir.actions.act_window',
            res_model: 'mail.activity',
            domain: [['state', '=', 'today']],
            view_mode: 'list,form',
            views: [[false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    show_overdue_activities(e) {
        e.stopPropagation();
        e.preventDefault();
        this.action.doAction({
            name: _t("Overdue Activities"),
            type: 'ir.actions.act_window',
            res_model: 'mail.activity',
            domain: [['state', '=', 'overdue']],
            view_mode: 'list,form',
            views: [[false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    show_activity_types(e) {
        e.stopPropagation();
        e.preventDefault();
        this.action.doAction({
            name: _t("Activity Type"),
            type: 'ir.actions.act_window',
            res_model: 'mail.activity.type',
            view_mode: 'list,form',
            views: [[false, 'list'], [false, 'form']],
            target: 'current',
        });
    }

    click_view(e) {
        const id = parseInt(e.target.value);
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: _t('Activity'),
            res_model: 'mail.activity',
            res_id: id,
            views: [[false, 'form']],
            view_mode: 'form',
            target: 'current',
        });
    }

    async click_origin(e) {
        const id = parseInt(e.target.value);
        const result = await this.orm.call('mail.activity', 'get_activity', [0, id], {});
        if (!result.model || !result.res_id) {
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: _t('Activity'),
            res_model: 'mail.activity',
            res_id: id,
            views: [[false, 'form']],
            view_mode: 'form',
            target: 'current',
        });
        return;
    }
        this.action.doAction({
            type: 'ir.actions.act_window',
            name: _t('Activity Origin'),
            res_model: result.model,
            res_id: result.res_id,
            views: [[false, 'form']],
            view_mode: 'form',
            target: 'current',
        });
    }
}

ActivityDashboard.template = "ActivityDashboard";
ActivityDashboard.components = { Layout };
registry.category("actions").add("activity_dashboard", ActivityDashboard);
