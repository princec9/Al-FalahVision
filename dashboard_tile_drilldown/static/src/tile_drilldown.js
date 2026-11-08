import { patch } from "@web/core/utils/patch";
import * as spreadsheet from "@odoo/o-spreadsheet";
import { navigateTo } from "@spreadsheet/actions/helpers";
import { Domain } from "@web/core/domain";

const SALE = [["state", "=", "sale"]];
const QUOTATION = [["state", "in", ["draft", "sent"]]];

// scorecard title -> pivot number + extra domain mirroring the PIVOT.VALUE args
const TILE_PIVOTS = {
    "Untaxed Amount": { pivot: "15", domain: SALE },
    "Tax inclusive": { pivot: "15", domain: SALE },
    "Average Order": { pivot: "15", domain: SALE },
    "Orders": { pivot: "15", domain: SALE },
    "Quotations": { pivot: "15", domain: QUOTATION },
};

// tiles with their own unique menu (exact match on last menu segment)
const MENU_PIVOTS = {
    "Down Payment Lines": { pivot: "18", domain: [] },
    "Invoiced excl. Down Payment Lines": { pivot: "20", domain: [] },
    "Invoices": { pivot: "14", domain: [] },
    "Orders with Discount": { pivot: "11", domain: [] },
};

patch(spreadsheet.components.ScorecardChart.prototype, {
    async navigateToOdooMenu(newWindow) {
        const getters = this.env.model.getters;
        const menu = getters.getChartOdooMenu(this.props.chartId);

        const def = getters.getChartDefinition(this.props.chartId);
        const title = (typeof def.title === "string" ? def.title : def.title?.text || "").trim();

        const menuName = menu?.name?.split("/").pop().trim();
        const cfg = TILE_PIVOTS[title] || MENU_PIVOTS[menuName];
        if (!cfg) {
            return super.navigateToOdooMenu(...arguments);
        }

        const pivotId = getters.getPivotId(cfg.pivot);
        if (!pivotId) {
            console.warn("drilldown: pivot not found", cfg.pivot);
            return super.navigateToOdooMenu(...arguments);
        }
        const pivot = getters.getPivot(pivotId);
        await pivot.load();
        const { actionXmlId, context } = getters.getPivotCoreDefinition(pivotId);

        const domain = Domain.and([
            pivot.getDomainWithGlobalFilters(),
            cfg.domain,
        ]).toList();

        await navigateTo(
            this.env,
            actionXmlId,
            {
                type: "ir.actions.act_window",
                name: menu?.name || title,
                res_model: pivot.definition.model,
                views: [[false, "list"], [false, "form"]],
                target: "current",
                domain,
                context,
            },
            { viewType: "list", newWindow }
        );
    },
});