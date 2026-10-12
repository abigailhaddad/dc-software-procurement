// Which NIGP commodity codes count as technology. Shared by technology.html,
// renewals.html and shared-vendors.html.
//
// The code set. A group lists either whole three-digit classes (`classes`)
// or single seven-digit item codes (`items`, with the description DC's
// contract pages give). The page filters on these and nothing else.
export const GROUPS = [
  {id: "software", label: "Software", on: true, classes: ["208", "209"],
    what: "Packaged software and licences."},
  {id: "services", label: "IT services", on: true, classes: ["920"],
    what: "Programming, data processing, hosting, system support. The largest group, and the loosest: it also holds data and consulting work that only touches a computer."},
  {id: "hardware", label: "Computer hardware", on: true, classes: ["204", "206", "207"],
    what: "Computers, servers, peripherals, accessories. 207 also holds some office supplies."},
  {id: "network", label: "Telecom and network equipment", on: true, classes: ["838", "839"],
    what: "Network switches, wireless access points, phones; on DC's site also some internet and wireless service."},
  {id: "upkeep", label: "Maintenance, repair and leasing", on: true, classes: ["939", "984"],
    what: "Repair and leasing of computers. 939 also covers office, photographic and radio/TV equipment, so it includes copier service and camera systems."},
  {id: "cloud", label: "Cloud software subscriptions", on: true, items: [
      ["9563575", "SUBSCRIPTIONS, SOFTWARE LICENSING, CLOUD BASED"]],
    what: "One item code in class 956, library and subscription services. Salesforce licences (through Carahsoft) and other cloud software are coded here."},
  {id: "other_items", label: "Other computer, software and web items", on: true, items: [
      ["8405670", "SOFTWARE, TELECONFERENCING SYSTEMS, AUDIO/VIDEO"],
      ["8833200", "Computer Telephony Integration (CTI) Systems"],
      ["9155133", "DEVELOPMENT SERVICES, INTERNET APPLICATION"],
      ["9155143", "HOSTING SERVICES, WEB SITE"],
      ["9159600", "Web Page Design and/or Management Services"],
      ["9159630", "DESIGN AND MANAGEMENT SERVICES, WEB PAGE"],
      ["9159634", "DEVELOPMENT SERVICES, WEB PAGE"],
      ["9159640", "MAINTENANCE SERVICES, WEBSITE"],
      ["9564022", "COMPUTER RELATED"],
      ["9564912", "COMPUTER RELATED"],
      ["9672700", "Computer Hardware Manufacturing Services"],
      ["9672800", "Computer Software Manufacturing Services"],
      ["9857400", "Software, Computer, Rental or Lease"]],
    what: "Item codes in TV equipment, communications, library, production and leasing classes. The largest by dollars is software rental or lease, mostly orders from INTRALOT INC."},
  {id: "consulting", label: "Computer consulting (class 918 items)", on: false, items: [
      ["9182800", "Computer Hardware Consulting"],
      ["9182810", "CONSULTANT SERVICES, COMPUTER SYSTEMS/NETWORKING"],
      ["9182900", "Computer Software Consulting"],
      ["9182930", "CONSULTING SERVICES RELATED TO THE IMPLEMENTATION OF SOFTWARE"],
      ["9183000", "Computer Network Consulting"]],
    what: "Unticked to start, because it is large and mixed. These codes carry the District's IT consulting and staffing orders (Computer Aid Inc. is the largest supplier), but some orders under them come from suppliers such as MAXIM HEALTHCARE SVCS and MIDTOWN PERSONNEL whose names suggest other work."},
];
// The ticked-by-default groups, flattened. The renewals and vendors pages use
// these for their "technology only" toggle, so all three pages count the same set.
export const TECH_CLASSES = GROUPS.filter((g) => g.on).flatMap((g) => g.classes || []);
export const TECH_CODES = GROUPS.filter((g) => g.on).flatMap((g) => (g.items || []).map((i) => i[0]));
