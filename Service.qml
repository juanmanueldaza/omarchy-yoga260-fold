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
  property bool hingeOk: false
  property bool folded: false
  property string mode: "book"
  property string orientationLabel: "landscape"
  // Flat enough that the accelerometer is blind to rotation in the plane of the
  // screen, so the panel says it cannot tell instead of naming a direction.
  property bool flat: false
  property var messages: []
  property var tilt: ({})
  // Always an object with the keys Fold.qml reads, so a widget that binds to it
  // before the first status line arrives sees an empty list rather than undefined.
  property var osk: ({ installed: [], drivable: false, auto: false, pluginId: "" })
  // Bumped on every line the daemon streams on its own, so Fold.qml can tell a
  // daemon that is talking from one that has gone quiet. A `status` reply does
  // not count: with the daemon hung that command still answers by probing the
  // hardware itself, which proves nothing about the daemon.
  property int statusSerial: 0

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

  function toggleKeyboard() {
    if (keyboardProc.running) return
    keyboardProc.command = [root.cli, "keyboard", "toggle"]
    keyboardProc.running = true
  }

  // A one-shot CLI call that hangs (a blocked hyprctl, a pipe that never
  // closes) would park its own `running` guard and drop every later call for
  // the rest of the session. One watchdog covers all of them: each process
  // restarts it as it starts, and a trigger releases whatever is still running
  // so the next call can try again. A call that finishes in time simply pushes
  // the deadline, and a trigger that finds nothing running does nothing.
  function releaseHungProcs() {
    const procs = [statusProc, rotateProc, lockProc, keyboardProc]
    for (let i = 0; i < procs.length; i++) {
      if (!procs[i].running) continue
      console.warn("omarchy-yoga260-fold: a CLI call did not answer in 5s; releasing it")
      procs[i].running = false
    }
  }

  function applyStatus(line, live) {
    var data
    try {
      data = JSON.parse(line)
    } catch (e) {
      return
    }
    root.supported = data.supported === true
    root.blockedBy = data.blockedBy || ""
    if (data.sensor) {
      root.hingeOk = data.sensor.hinge ? data.sensor.hinge.ok === true : false
    }
    const state = data.state || {}
    root.folded = state.folded === true
    root.mode = state.mode || "book"
    root.orientationLabel = state.orientationLabel || ""
    root.flat = state.flat === true
    root.messages = state.messages || []
    root.tilt = state.tiltDeg || {}
    root.osk = data.osk && data.osk.installed ? data.osk
      : { installed: [], drivable: false, auto: false, pluginId: "" }
    if (live) root.statusSerial++
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
        root.applyStatus(line, true)
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
    onRunningChanged: if (running) cliWatchdog.restart()
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.applyStatus(text, false)
    }
    stderr: StdioCollector {
      waitForEnd: true
    }
  }

  Process {
    id: rotateProc
    onRunningChanged: if (running) cliWatchdog.restart()
    onExited: root.refresh()
  }

  Process {
    id: lockProc
    onRunningChanged: if (running) cliWatchdog.restart()
    onExited: root.refresh()
  }

  Process {
    id: keyboardProc
    onRunningChanged: if (running) cliWatchdog.restart()
    onExited: root.refresh()
  }

  // One shot: armed by a process starting, rearmed by the next one. A healthy
  // call never lets it fire, and one that does fire finds only hung processes
  // to release (or none, and stops there).
  Timer {
    id: cliWatchdog
    running: false
    interval: 5000
    repeat: false
    onTriggered: root.releaseHungProcs()
  }
}
