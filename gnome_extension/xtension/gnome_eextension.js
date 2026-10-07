import Gio from 'gi://Gio';
import Shell from 'gi://Shell';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const INTERFACE = `<node><interface name="org.crankyclippy.Focus">
<method name="Get"><arg type="s" direction="out" name="window"/></method>
</interface></node>`;

// Read-only local session API. No Eval, window control, geometry or network.
export default class ClippyFocus extends Extension {
    Get() {
        if (Main.sessionMode.isLocked || Main.overview.visible)
            return JSON.stringify({no_active: true});
        const window = global.display.focus_window;
        if (!window)
            return JSON.stringify({no_active: true});
        const app = Shell.WindowTracker.get_default().get_window_app(window);
        const desktopId = app?.get_id() || null;
        return JSON.stringify({
            app_class: window.get_wm_class() || desktopId || window.get_wm_class_instance() || '',
            caption: window.get_title() || '',
            window_id: String(window.get_stable_sequence()),
            desktop_file_id: desktopId,
        });
    }

    enable() {
        this._dbus = Gio.DBusExportedObject.wrapJSObject(INTERFACE, this);
        this._dbus.export(Gio.DBus.session, '/org/crankyclippy/Focus');
    }

    disable() {
        this._dbus?.unexport();
        this._dbus = null;
    }
}
