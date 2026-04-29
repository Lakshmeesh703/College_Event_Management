# Final Project Report

## Project Title
College Event Statistics Portal

## Abstract
The College Event Statistics Portal is a web-based platform designed to manage college events, participant registrations, event results, and analytics from a single system. The portal supports multiple user roles, including students, coordinators, conveners, management staff, and administrators. Each role receives a tailored interface and permission set so that event operations remain organized, secure, and easy to monitor.

The project combines Flask, SQLAlchemy, Jinja2 templates, Pandas, and charting libraries to deliver both operational features and meaningful analytics. In addition to standard event management features, the system now includes a stronger analytics dashboard with filters, KPI cards, trend charts, school-wise comparisons, department-wise insights, weekday registration analysis, and top competition summaries. This makes the portal suitable not only for administration but also for decision-making and reporting.

## 1. Introduction
Colleges usually conduct many events across departments, schools, and student groups. Without a central platform, tracking registrations, participants, results, and performance trends becomes time-consuming and inconsistent. This project was built to solve that problem by providing a structured portal for event administration and analytics.

The application allows users to browse public event pages, register for events, manage participant records, upload brochures, enter results, and view statistics through a role-based analytics dashboard. The system was also improved to make analytics less static and more useful for actual planning. Instead of only showing tables, the dashboard now presents meaningful comparisons and trend patterns that help users understand what kinds of events attract participation and where engagement is strongest.

## 2. Objectives
The main objectives of the project are:

- Centralize college event management in one web application.
- Support different user roles with controlled access to features.
- Provide public event browsing and results lookup.
- Allow coordinators and administrators to create and edit events and competitions.
- Store and display event brochures in PDF format.
- Deliver analytics that help management understand participation patterns.
- Make reporting and export easy for official submission and review.

## 3. System Overview
The portal is built as a Flask application with a clear separation between backend logic, database models, and template-based frontend pages. Users log in and are redirected to pages based on their role.

The system includes the following major modules:

- Authentication and role-based access control.
- Event and competition management.
- Participant registration and result recording.
- Brochure upload and inline viewing.
- Analytics dashboard and export tools.
- Administrative dashboards for management and convener workflows.

The user experience is designed to be practical. Students can explore events and view brochures. Coordinators can manage allotted events. Conveners can work within their school scope. Management and administrators can oversee the entire institution and review analytics across all visible data.

## 4. Database Design
The database uses normalized tables so that event data, participant data, registrations, and results are stored separately. This avoids duplication and keeps the system maintainable.

The core entities include:

- `users` for login credentials and role assignment.
- `events` for event details, school, category, venue, and brochure path.
- `competitions` for sub-events or event-specific competitions.
- `participants` for student and external participant profiles.
- `event_participation` for registration records.
- `results` for ranks and prizes.
- `activity_logs` for auditing important actions.

The database design supports reporting at multiple levels: event-wise, competition-wise, school-wise, department-wise, and user-role-wise. This structure is important because the analytics dashboard now aggregates participation from different views depending on the logged-in user.

## 5. Key Functional Modules

### 5.1 Authentication and access control
The portal uses role-based control so that each user sees only the actions they are allowed to perform. This includes coordinator, convener, management, admin, and student access. Permissions are enforced in the backend, not just in the interface, which keeps the application safer and more reliable.

### 5.2 Event and competition management
Users with proper permissions can create events, edit event details, and manage associated competitions. The system also supports competition editing for administrators and conveners where applicable. This is useful when event schedules, rules, or competition details change after the initial setup.

### 5.3 Brochure upload and viewing
The portal supports PDF brochure uploads for events and competitions. Brochures are stored on the server and can be viewed inline in the browser rather than forcing a download. This improves usability for students and staff who want to quickly review the event details.

### 5.4 Participant registration and result publishing
Students can register for events through the application. Coordinators and management users can record results and monitor participation counts. This creates a complete flow from event announcement to participation tracking and final outcomes.

### 5.5 Analytics and reporting
This is the strongest area of the improved system. The analytics dashboard now provides:

- Total events, total schools, total participants, and average registrations per event.
- Event-wise and school-wise comparisons.
- Monthly event trend charts.
- Internal versus external participation split.
- Department-wise registration analysis.
- Year-wise student registration analysis.
- Top active students.
- Top events by registrations.
- Top competitions by registrations.
- Weekday registration analysis for pattern detection.
- Filtered exports in CSV format for administrative reporting.

These analytics are role aware. Management sees the broadest view, while conveners and coordinators see only the data that belongs to their scope.

## 6. Implementation Highlights
The backend is implemented in Flask using blueprints and SQLAlchemy models. The analytics module computes summary statistics directly from the database and formats them for both HTML tables and chart visualizations. Pandas is used for structured aggregation, while Matplotlib and Chart.js support visual representation.

One important improvement in this version is the addition of more actionable analytics. For example, the dashboard now highlights the busiest weekdays, top competitions, and participation intensity. These extra indicators make the dashboard more useful than a simple static summary because they help identify recurring participation behavior and high-performing event types.

Another important enhancement is inline brochure viewing. Instead of treating PDF brochures as only downloadable files, the system now serves them in a browser-friendly format. This makes the portal more convenient for students and reviewers.

## 7. User Interface Design
The interface uses card-based sections, clear headings, filter controls, and compact KPI blocks so that information is easy to scan. The analytics page was improved to avoid looking empty or repetitive. It now mixes:

- summary cards for quick reading,
- chart panels for trends,
- tables for exact values,
- and export buttons for reporting.

This balance is important because different users need different kinds of information. Management users often need quick insights, while coordinators may need exact counts and downloadable records.

## 8. Testing and Validation
The project changes were validated using syntax checks and a live browser run of the application. The analytics dashboard was opened successfully after login, and the new chart areas were confirmed to be present on the page. The modified analytics route also passed Python compilation checks, which helped verify that the implementation was structurally correct.

In practical terms, the dashboard now behaves as expected in the local environment: filters load, summary values appear, charts render, and the additional analytics sections are visible to authenticated management users.

## 9. Deployment Readiness
The repository includes deployment support files such as `Procfile`, `render.yaml`, and `runtime.txt`. The application is designed to run with PostgreSQL or Supabase-style database configuration, and the local run script prepares the environment consistently.

This makes the system suitable for deployment on a cloud platform with minimal extra setup. The codebase also keeps the analytics and brochure-handling logic within the existing Flask structure, which reduces deployment complexity.

## 10. Future Scope
The project can be extended further with the following improvements:

- Drill-down analytics when a user clicks on a chart segment.
- PDF report generation for formal submissions.
- Scheduled email reports for management.
- Search and recommendation support for events.
- Attendance tracking and post-event feedback analytics.
- More advanced filters such as school, department, date range, and category combinations.

These additions would make the portal even more useful as a long-term college event intelligence system.

## 11. Conclusion
The College Event Statistics Portal successfully brings together event management, participant registration, result publishing, brochure handling, and analytics in one application. The latest improvements made the analytics section stronger, more visual, and more useful for management and administrators. Instead of basic tables alone, the dashboard now provides a clearer picture of event performance through KPIs, charts, and comparison sections.

Overall, the project demonstrates how a college event portal can be transformed into a practical decision-support system. It is not only a registration platform but also a reporting and analysis tool that helps the institution understand event participation trends and improve planning in future events.

---

## Submission Note
This report is formatted for direct submission. If needed, it can be copied into a document editor and exported as PDF for final college submission.
