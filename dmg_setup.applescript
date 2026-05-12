-- Bless the DMG layout through Finder so modern macOS (Sonoma / Tahoe)
-- actually respects the saved window bounds and icon positions.
--
-- dmgbuild calls this with the volume name as argv[1] after staging
-- the files. We can't use `tell disk "VOLNAME"` because some Finder
-- preference setups (specifically "Show external disks on Desktop"
-- disabled) hide DMG-mounted volumes from Finder's `disk` collection,
-- and the script then fails with "-1728: can't get object". Working
-- through POSIX path + `tell front window` bypasses that index.

on run argv
    set volumeName to item 1 of argv
    set volumeAlias to (POSIX file ("/Volumes/" & volumeName)) as alias

    -- Trace to stderr so dmgbuild's build output proves we actually ran.
    do shell script "echo '[dmg_setup] applying layout to /Volumes/" & volumeName & "' 1>&2"

    tell application "Finder"
        activate
        open volumeAlias
        delay 1

        tell front window
            set current view to icon view
            set toolbar visible to false
            set statusbar visible to false
            -- bounds = {x1, y1, x2, y2}. window_rect in dmg_settings.py
            -- is ((100, 120), (540, 380)), so x2 = 100+540, y2 = 120+380.
            set the bounds to {100, 120, 640, 500}
        end tell

        -- The background picture is already pinned by dmgbuild in
        -- .DS_Store (backgroundImageAlias + backgroundType=2). Modern
        -- Finder honours that part fine; the bits that DON'T stick
        -- without a Finder-driven save are window bounds and chrome
        -- visibility — that's all this script targets. Setting
        -- `background picture` via AppleScript on a hidden file
        -- (.background.png) raises -1728 on Tahoe ("can't get object"),
        -- so we skip it.
        set theViewOptions to the icon view options of front window
        tell theViewOptions
            set arrangement to not arranged
            set icon size to 128
            set text size to 13
        end tell

        -- Pin the two visible items exactly where the painted arrow
        -- expects them (matches icon_locations in dmg_settings.py).
        set position of item "YT-sub.app" of front window to {130, 200}
        set position of item "Applications" of front window to {410, 200}

        -- Force Finder to flush layout to .DS_Store. The literal verb
        -- is `update` on the *folder*, not the window — `update window`
        -- raises -1708 on Tahoe.
        update (folder of front window) without registering applications
        delay 1
        close front window
    end tell
end run
