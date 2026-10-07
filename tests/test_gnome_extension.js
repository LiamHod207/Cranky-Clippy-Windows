// Offline API-shape regression test, not a GNOME runtime test.
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
let exported, unexported = false;
const context = {
    Gio: {DBus: {session: {}}, DBusExportedObject: {wrapJSObject: () => ({
        export: (_, path) => { exported = path; }, unexport: () => { unexported = true; },
    })}},
    Shell: {WindowTracker: {get_default: () => ({get_window_app: () => ({get_id: () => 'org.mozilla.firefox.desktop'})})}},
    Extension: class {}, Main: {sessionMode: {isLocked: false}, overview: {visible: false}},
    global: {display: {focus_window: {
        get_wm_class: () => 'firefox', get_wm_class_instance: () => 'firefox',
        get_title: () => 'Coding - Firefox', get_stable_sequence: () => 42,
    }}},
};
let source = fs.readFileSync('gnome_extension/extension.js', 'utf8')
    .replace(/^import .*;\n/gm, '').replace('export default class', 'class');
source += '\nthis.extension = new ClippyFocus();';
vm.runInNewContext(source, context);
const extension = context.extension;
extension.enable();
assert.equal(exported, '/org/crankyclippy/Focus');
assert.equal(JSON.parse(extension.Get()).desktop_file_id, 'org.mozilla.firefox.desktop');
context.Main.overview.visible = true;
assert.equal(JSON.parse(extension.Get()).no_active, true);
context.Main.overview.visible = false;
context.Main.sessionMode.isLocked = true;
assert.equal(JSON.parse(extension.Get()).no_active, true);
context.Main.sessionMode.isLocked = false;
context.global.display.focus_window = null;
assert.equal(JSON.parse(extension.Get()).no_active, true);
extension.disable();
assert(unexported);
console.log('GNOME extension mocked lifecycle/focus checks passed');
