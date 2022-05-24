// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.views.calendar["Action"] = {
	field_map: {
		"start": "start",
		"end": "due",
		"id": "name",
		"title": 'name',
		"allDay": "allDay",
		"progress": "progress"

	},
	gantt: {
        order_by: "start"
    },
	filters: [
		{
			"fieldtype": "Link",
			"fieldname": "project",
			"options": "Project",
			"label": __("Project")
		}
	],
	get_events_method: "frappe.desk.calendar.get_events"
	
}

