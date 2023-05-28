// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.views.calendar["Deliverables"] = {
	field_map: {
		"id": "name",
		"end": "eng_complete_date",
		"start": "eng_start_d",
		"title": "deliv_title",
		"allDay": "allDay",
		"status": "eng_status",
		"color":"color",
		"progress": "progress"
	},
	gantt: true,
	filters: [
		{
			"fieldtype": "Link",
			"fieldname": "deliv_title",
			"options": "Project",
			"label": __("deliv_title")
		}
	],
	get_events_method: "frappe.desk.calendar.get_events"

}

