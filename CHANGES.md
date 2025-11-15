# Kali Tools Manager - Recent Changes

## Overview
This document summarizes the recent enhancements made to the Kali Tools Manager script.

## 1. Fixed Web Scraper for Complete Tool Coverage

### Problem
The previous scraper only discovered ~13 tools instead of the full catalog from https://www.kali.org/tools/all-tools/

### Solution
- **Simplified link collection**: Removed complex pagination logic since the page appears to be a single long list
- **Improved link filtering**: Changed from counting slashes to validating the exact URL structure
  - Pattern: `/tools/<toolname>/` (exactly 2 parts: 'tools' and toolname)
  - Strips anchor fragments to avoid duplicates (e.g., `/tools/apache2/#apache2-bin` → `/tools/apache2/`)
- **Direct targeting**: Points directly to `/tools/all-tools/` instead of trying multiple index patterns
- **Better error handling**: Logs errors to stderr without crashing the application

### Code Changes
- Modified `_fetch_kali_tool_links()` method in `KaliToolsManager` class
- Simplified from ~60 lines to ~40 lines with clearer logic

## 2. Added Sub-Package Support

### Feature Description
Many Kali tools come with multiple related packages (e.g., apache2 has apache2-bin, apache2-dev, apache2-data). This feature allows users to view and manage these sub-packages independently.

### Implementation

#### A. Extended Data Model
- Added `subpackages: List[str]` field to the `Tool` dataclass
- Maintains backward compatibility with existing dict-like access

#### B. Enhanced Parsing
- Modified `_parse_tool_page_for_package()` to return a 3-tuple: `(package_name, category, subpackages_list)`
- Extracts sub-packages by looking for anchor links like `/tools/<toolname>/#<subpackage>`
- Validates package names with regex pattern: `^[a-z0-9][a-z0-9+\-.]{2,}$`
- Filters out duplicates and the main package name

#### C. Updated Discovery
- Modified `discover_from_kali_site()` to unpack the new 3-tuple and populate the `subpackages` field
- Preserves the `subpackages` data in JSON persistence

#### D. Enhanced Detail View
- **Main Details Panel**: Shows list of related packages with their installation status
  - Format: `[✓/✗] package-name`
  - Displays up to 10 sub-packages, with "... and N more" indicator
- **Interactive Options**: Dynamic menu based on package state and sub-package availability
  - If tool has sub-packages: "1) Install/Uninstall Main  2) Manage Sub-packages  3) Remove from List  4) Back"
  - If no sub-packages: "1) Install/Uninstall Main  2) Remove from List  3) Back"

#### E. New Sub-Package Management Menu
- Added `_manage_subpackages()` method for interactive sub-package control
- Features:
  - **Table View**: Lists all sub-packages with numbers and installation status
  - **Individual Install/Uninstall**: Enter package number to toggle installation
  - **Bulk Operations**:
    - Press 'A' to install all uninstalled sub-packages
    - Press 'U' to uninstall all installed sub-packages
  - **Navigation**: Press 'Q' to return to main detail view
- Real-time status updates and toast notifications

### User Experience

#### Viewing Tools with Sub-Packages
1. Browse tools normally (e.g., apache2, impacket-scripts)
2. Press ENTER or D to view details
3. See "Related Packages" section with installation status

#### Managing Sub-Packages
1. In tool details, select option "2) Manage Sub-packages"
2. See numbered list of all related packages
3. Choose action:
   - Enter number (e.g., "3") to install/uninstall that specific package
   - Enter 'A' to install all available packages
   - Enter 'U' to uninstall all installed packages
   - Enter 'Q' to go back

### Benefits
- **Granular Control**: Install only the components you need
- **Visibility**: Clear view of all related packages and their status
- **Efficiency**: Bulk operations for large tool suites (e.g., john with 50+ utilities)
- **Consistency**: Same install/uninstall workflow as main packages

## Technical Details

### Data Flow
1. **Discovery**: Web scraper extracts main package and sub-packages from tool page
2. **Storage**: JSON persistence includes `subpackages` field
3. **Display**: Detail view queries installation status for each sub-package dynamically
4. **Management**: Sub-package operations use the same `install_tool()`/`uninstall_tool()` methods

### Backward Compatibility
- Tools without sub-packages work exactly as before
- Old JSON files missing the `subpackages` field are handled gracefully (defaults to empty list)
- Dict-like access preserved: `tool['subpackages']` works alongside `tool.subpackages`

## Testing Recommendations

On a Kali Linux system:
1. **Test Scraper**: Clear cache and run updates menu option "Refresh tool list from all sources"
   - Verify hundreds of tools are discovered (not just ~13)
   - Check console output for any error messages
2. **Test Sub-Packages**: Find a multi-package tool (e.g., apache2, john, impacket-scripts)
   - View details and verify "Related Packages" section appears
   - Try "Manage Sub-packages" option
   - Install/uninstall individual packages
   - Test bulk install/uninstall operations
3. **Test Persistence**: Close and reopen the app to ensure sub-packages are saved and loaded

## Files Modified
- `kalitools.py`: Main application file (~2890 lines)
  - Modified: `Tool` dataclass (line ~40)
  - Modified: `_fetch_kali_tool_links()` (line ~1230)
  - Modified: `_parse_tool_page_for_package()` (line ~1280)
  - Modified: `discover_from_kali_site()` (line ~1366)
  - Modified: `show_tool_details()` (line ~2561)
  - Added: `_manage_subpackages()` (line ~2692)

## Future Enhancements
- Add command-line flags to list sub-packages: `--list-subpackages <tool>`
- Consider showing sub-package commands in the commands list
- Add sub-package filtering in main tool list view
- Include sub-package size information where available
