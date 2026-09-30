import QtQuick
import Quickshell
import Quickshell.Io

// The fold daemon, mounted once for the shell.
//
// Omarchy builds a bar per monitor, so a Process living on the widget would be
// one daemon per screen, each reading the same two sensors and each turning the
// same panel. The daemon and the state everything reads belong here.
Item {
  id: root
  width: 0
  height: 0
  visible: false

  // Observed state. The daemon prints a JSON line whenever any of it changes.
  property bool supported: false
  property string blockedBy: ""
  property bool sensorAccel: false
  property bool sensorHinge: false
  property bool hingeOk: false
  property bool folded: false
  property string mode: "laptop"
  property string orientation: "normal"
  property string orientationLabel: "landscape"
  // Flat enough that the accelerometer is blind to rotation in the plane of the
  // screen, so the panel says it cannot tell instead of naming a direction.
  property bool flat: false
  property real flatDeg: 0.0
  // Named panelTransform, not transform: `transform` is a FINAL property on the
  // base Item and cannot be redeclared.
  property int panelTransform: 0
  property bool verified: false
  property bool still: true
  property bool keyboardDisabled: false
  property bool pointersDisabled: false
  property var messages: []
  property string panel: "eDP-1"
  property string touch: ""
  property string pen: ""
  property var tilt: ({})
  // Always an object with the keys Fold.qml reads, so a widget that binds to it
  // before the first status line arrives sees an empty list rather than undefined.
  property var osk: ({ installed: [], drivable: false, auto: false, pluginId: "" })

  // How long to wait before bringing a crashed daemon back.
  property int restartDelay: 2000

  readonly property string cli: decodeURIComponent(
    Qt.resolvedUrl("bin/omarchy-yoga260-fold").toString().replace("file://", ""))

  function refresh() {
    if (!statusProc.running) statusProc.running = true
  }

  // The widget calls straight into this object rather than spawning its own
  // processes: one bar per monitor means a tap could otherwise arrive three
  // times, and the panel is turned three times over.
  function rotate(position) {
    if (rotateProc.running) return
    rotateProc.command = [root.cli, "rotate", position]
    rotateProc.running = true
  }

  function toggleLock() {
    if (lockProc.running) return
    lockProc.command = [root.cli, "lock", "toggle"]
    lockProc.running = true
  }

  function setMapping(value) {
    if (mappingProc.running) return
    mappingProc.command = [root.cli, "mapping", value]
    mappingProc.running = true
  }

  function setSetting(name, value) {
    if (settingProc.running) return
    settingProc.command = [root.cli, "setting", name, value]
    settingProc.running = true
  }

  function toggleKeyboard() {
    if (keyboardProc.running) return
    keyboardProc.command = [root.cli, "keyboard", "toggle"]
    keyboardProc.running = true
  }

  function applyStatus(line) {
    var data
    try {
      data = JSON.parse(line)
    } catch (e) {
      return
    }
    root.supported = data.supported === true
    root.blockedBy = data.blockedBy || ""
    if (data.sensor) {
      root.sensorAccel = data.sensor.accel === true
      root.sensorHinge = data.sensor.hinge ? data.sensor.hinge.available === true : false
      root.hingeOk = data.sensor.hinge ? data.sensor.hinge.ok === true : false
    }
    const state = data.state || {}
    root.folded = state.folded === true
    root.mode = state.mode || "laptop"
    root.orientation = state.orientation || "normal"
    root.orientationLabel = state.orientationLabel || ""
    root.flat = state.flat === true
    root.flatDeg = Number(state.flatDeg) || 0
    root.panelTransform = Number(state.transform) || 0
    root.verified = state.verified === true
    root.still = state.still !== false
    root.keyboardDisabled = state.keyboardDisabled === true
    root.pointersDisabled = state.pointersDisabled === true
    root.messages = state.messages || []
    root.panel = state.panel || ""
    root.touch = state.touch || ""
    root.pen = state.pen || ""
    root.tilt = state.tiltDeg || {}
    root.osk = data.osk && data.osk.installed ? data.osk
      : { installed: [], drivable: false, auto: false, pluginId: "" }
  }

  Component.onCompleted: daemonProc.running = true

  Process {
    id: daemonProc
    command: [root.cli, "daemon"]
    stdout: SplitParser {
      onRead: function(line) {
        // A daemon that reports is a daemon that started; the next crash gets
        // the short pause again.
        root.restartDelay = 2000
        root.applyStatus(line)
      }
    }
    stderr: SplitParser {
      onRead: function(line) { console.info(line) }
    }
    // After a crash, come back. The pause doubles, up to a minute, while the
    // daemon keeps failing before it reports, so one that cannot start does
    // not spin.
    onExited: {
      daemonRestart.interval = root.restartDelay
      daemonRestart.restart()
      root.restartDelay = Math.min(root.restartDelay * 2, 60000)
    }
  }

  Timer {
    id: daemonRestart
    onTriggered: if (!daemonProc.running) daemonProc.running = true
  }

  Process {
    id: statusProc
    command: [root.cli, "status"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.applyStatus(text)
    }
    stderr: StdioCollector {
      waitForEnd: true
    }
  }

  Process {
    id: rotateProc
    onExited: root.refresh()
  }

  Process {
    id: lockProc
    onExited: root.refresh()
  }

  Process {
    id: mappingProc
    onExited: root.refresh()
  }

  Process {
    id: settingProc
    onExited: root.refresh()
  }

  Process {
    id: keyboardProc
    onExited: root.refresh()
  }
}
