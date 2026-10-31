# Changelog

Kith follows semantic versioning. Releases are on GitHub.

## Unreleased

- Census by year (`/census`): each census year's households, by place, linked from Places.
- Census matches (Admin > Tidy the tree): suggests which person in the tree each name typed in a census
  transcription is, for an editor to confirm; a confirmed person gets that census on their page, and a birth year
  from the census age can be filled in. Database upgrade 0004.
- Places: "Everyone in" an area (the events in a place and every place inside it). A place with no coordinates
  shows on maps at the place it sits in, marked approximate.
- Tree opens an hourglass chart of you; people not linked to their record start at a person chosen under
  Admin > Site settings.
- Person page: a woman's married names ("Later Mary REED"), and newspaper searches (Chronicling America,
  Newspapers.com) next to the other outside links.
- Where the lines begin (`/lines`): the earliest person of each surname with no parents in the tree. Person pages
  say how many generations down from the start of their surname line they are.
- Relationship page: the path as a row of faces, from one person up to the common ancestor and down to the other.
- Person map: a line through the places of a life, in the order of the events, with the order written under it.
- Fan chart: photos in the wedges, and Print poster (just the fan, landscape).
- Admin: What's changed, the newest changes to the site by date.
- Data checks: death date differs from the obituary.
- Branch book (`/book/<id>`) and family map (`/map`).
- Hourglass: spouses shown, and an Hourglass button on person pages. Place page map.
- Tidy places: possible misspellings, and moving the real place out of census notes where a record title sits in
  the place field. Surname pair suggestions.
- Banner picture choice, and light, dark or device mode for every theme. More contrast in the light themes.
- Your account: unlink yourself from the tree. Version and commit in the footer. ID tooltip on person and family
  links.
- The person picker now lists name matches on Android Chrome (it used to show nothing until you typed an ID).
- Tagging a face offers quick buttons for the family of the people on the photo: parents, siblings, spouses and
  children.

## 0.1.0 (2026-10-29)

First public release.
