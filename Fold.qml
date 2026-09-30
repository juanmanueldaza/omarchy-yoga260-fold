import QtQuick
import Quickshell
import qs.Commons
import qs.Ui

// The bar button and the panel it opens.
//
// Named for what it is about rather than for the lock it started as, because
// the shell caches a plugin's directory and an entry point renamed by an update
// fails to load until the shell restarts.
//
// The rotation itself needs no button: the hinge sensor handles it. What the
// panel is for is the two things a person holding a folded machine still
// reaches for -- the on-screen keyboard, and freezing or turning the screen --
// and it has to work under a finger, because a folded machine has no mouse and
// no middle button. So a tap opens it and every control in it is a big target.
Panel {
  id: root
  moduleName: "estrocondoso.yoga260-fold"
  ipcTarget: "estrocondoso.yoga260-fold"

  readonly property var service: {
    var host = bar && bar.shell ? bar.shell : null
    if (!host || typeof host.serviceFor !== "function") return null
    return host.serviceFor("estrocondoso.yoga260-fold")
  }

  readonly property bool supported: service ? service.supported : false
  readonly property string blockedBy: service ? service.blockedBy : ""
  readonly property bool sensorAccel: service ? service.sensorAccel : false
  readonly property bool sensorHinge: service ? service.sensorHinge : false
  readonly property bool hingeOk: service ? service.hingeOk : false
  readonly property bool folded: service ? service.folded : false
  readonly property string mode: service ? service.mode : "book"
  readonly property string orientationLabel: service ? service.orientationLabel : ""
  // The machine is flat enough that the accelerometer cannot see which way it is
  // turned in its own plane. Gravity points the same way however the machine is
  // spun on the desk, so any direction claimed here would be invented.
  readonly property bool flat: service ? service.flat : false
  readonly property real flatDeg: service ? service.flatDeg : 0
  readonly property bool locked: setting("locked", false)
  readonly property bool lockKeyboard: setting("lockKeyboard", true)
  readonly property bool lockPointers: setting("lockPointers", true)
  readonly property bool oskAuto: setting("oskAuto", false)
  readonly property var messages: service ? service.messages : []
  readonly property var tilt: service ? service.tilt : ({})
  readonly property var osk: service && service.osk ? service.osk
    : { installed: [], drivable: false, auto: false, pluginId: "" }

  // ----------------------------------------------------------------- settings

  // The daemon re-reads these from shell.json and follows the file, so saving
  // one is all it takes to apply it; nothing here has to restart it.
  readonly property string mapping: setting("mapping", "standard")

  function save(name, value) {
    const change = {}
    change[name] = value
    root.settings = Object.assign({}, root.settings, change)
    if (root.bar && root.bar.shell) root.bar.shell.updateEntryInline(root.moduleName, root.settings)
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // The line under the name. It reports what the sensors actually support and
  // says so when they do not.
  //
  // This used to answer "Landscape while open" for book mode, which was not a
  // reading of anything: it was a hardcoded string chosen by the lid being
  // open, so it claimed landscape for a machine lying flat and spun to portrait,
  // which is precisely the case the accelerometer cannot resolve. A machine on
  // a desk has no in-plane orientation to report at all -- gravity points the
  // same way however it is turned -- and saying so is more use than a
  // confident wrong answer.
  readonly property string status: {
    if (!supported) return blockedBy || "Not this model"
    if (messages && messages.length > 0) return messages[0]
    if (locked) return "Rotation locked"
    if (!hingeOk) return "Hinge sensor unsettled"
    if (flat) return "Flat · cannot tell which way it is turned"
    if (mode === "book") return "Laptop · " + (orientationLabel || "landscape")
    return "Following the hinge · " + (orientationLabel || "")
  }

  // ---------------------------------------------------------- keyboard cursor

  // The rows the cursor walks, top to bottom. Each has as many cells as it has
  // buttons; a toggle row has one.
  readonly property var rows: {
    const list = []
    if (root.osk.installed.length > 0) list.push({ id: "keyboard", cells: 1 })
    list.push({ id: "screen", cells: 3 }, { id: "devices", cells: 2 }, { id: "settings", cells: 1 })
    if (settingsShown) {
      list.push({ id: "mapping", cells: mappingOptions.length })
    }
    return list
  }
  property bool settingsShown: false
  property int cursorRow: 0
  property int cursorCell: 0
  property bool cursorActive: false

  function cursorOn(rowId, cell) {
    return cursorActive && rows[cursorRow] && rows[cursorRow].id === rowId
      && (cell === undefined || cursorCell === cell)
  }

  function point(rowId, cell) {
    for (let i = 0; i < rows.length; i++) {
      if (rows[i].id !== rowId) continue
      cursorActive = true
      cursorRow = i
      cursorCell = cell || 0
    }
  }

  function moveCursor(dx, dy) {
    if (!cursorActive) { cursorActive = true; return }
    if (dy !== 0) {
      cursorRow = Math.max(0, Math.min(rows.length - 1, cursorRow + dy))
      cursorCell = Math.min(cursorCell, rows[cursorRow].cells - 1)
    } else {
      cursorCell = Math.max(0, Math.min(cursorCell + dx, rows[cursorRow].cells - 1))
    }
  }

  function activateCursor() {
    if (!cursorActive) return
    const row = rows[cursorRow].id
    if (row === "screen") screenActions[cursorCell].run()
    else if (row === "settings") settingsShown = !settingsShown
    else if (row === "keyboard") { if (service) service.toggleKeyboard() }
    else if (row === "devices") deviceActions[cursorCell].run()
    else if (row === "mapping") save("mapping", mappingOptions[cursorCell].value)
  }

  onOpenedChanged: {
    if (!opened) return
    cursorActive = false
    settingsShown = false
    cursorRow = 0
    cursorCell = 0
    if (service) service.refresh()
  }
  onRowsChanged: if (cursorRow >= rows.length) cursorRow = rows.length - 1

  // ------------------------------------------------------------------ choices

  // A Hyprland transform counts quarter turns counter-clockwise, so "next"
  // turns the picture left.
  readonly property var screenActions: [
    { label: "Left", icon: "󰑅", run: function() { if (root.service) root.service.rotate("prev") } },
    { label: root.locked ? "Unlock" : "Lock", icon: root.locked ? "󰒁" : "󰌋",
      run: function() { if (root.service) root.service.toggleLock() } },
    { label: "Right", icon: "󰑇", run: function() { if (root.service) root.service.rotate("next") } }
  ]

  readonly property var deviceActions: [
    { label: root.lockKeyboard ? "Keys stay off" : "Keys off when folded",
      icon: "󰌌",
      run: function() { root.save("lockKeyboard", !root.lockKeyboard) } },
    { label: root.oskAuto ? "Keyboard follows fold" : "Keyboard on fold",
      icon: "󰌨",
      run: function() { root.save("oskAuto", !root.oskAuto) } }
  ]

  readonly property var mappingOptions: [
    { value: "standard", label: "Standard" },
    { value: "portrait-swapped", label: "Upright" },
    { value: "landscape-swapped", label: "Flat" },
    { value: "rotated-180", label: "180" }
  ]

  // ------------------------------------------------------------------- button

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    // nf-md-screen_rotation_lock / nf-md-screen_rotation
    text: root.locked ? "󰒈" : "󰒵"
    active: root.locked
    tooltipText: root.opened ? "" : root.status
    // A tap opens the panel. With a mouse, the middle button still turns the
    // screen without opening anything.
    onPressed: function(b) {
      if (b === Qt.MiddleButton) { if (root.service) root.service.rotate("next") }
      else root.toggle()
    }
  }

  // -------------------------------------------------------------------- panel

  KeyboardPanel {
    id: panel
    anchorItem: button
    owner: root
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(360))
    contentHeight: panel.fittedContentHeight(column.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onMoveRequested: function(dx, dy) { root.moveCursor(dx, dy) }
      onActivateRequested: root.activateCursor()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Column {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        spacing: Style.space(14)

        // ---------- Hero: icon, name and state, orientation ----------
        Item {
          width: parent.width
          implicitHeight: Math.max(heroIcon.implicitHeight, heroLabels.implicitHeight)

          Text {
            id: heroIcon
            textFormat: Text.PlainText
            // nf-md-tablet / nf-md-laptop / nf-md-tablet-cellphone for a tent
            text: root.folded ? "\u{F04F6}" : (root.mode === "tent" ? "\u{F0F5B}" : "\u{F0322}")
            color: root.bar.foreground
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.display
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
          }

          Column {
            id: heroLabels
            anchors.left: heroIcon.right
            anchors.leftMargin: Style.space(14)
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            spacing: Style.space(2)

            Text {
              textFormat: Text.PlainText
              text: root.folded ? "Tablet" : (root.mode === "tent" ? "Tent" : "Laptop")
              color: root.bar.foreground
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.title
              font.bold: true
              elide: Text.ElideRight
              width: parent.width
            }

            Text {
              textFormat: Text.PlainText
              text: (root.status + (root.folded && root.orientationLabel
                ? " · " + root.orientationLabel : "")).toUpperCase()
              color: Qt.darker(root.bar.foreground, 1.4)
              font.family: root.bar.fontFamily
              font.pixelSize: Style.font.caption
              font.bold: true
              font.letterSpacing: 1.2
              elide: Text.ElideRight
              width: parent.width
            }
          }
        }

        // Why the screen is not turning is the first thing worth saying when it
        // is not turning, so it goes above the controls rather than below.
        Text {
          visible: !root.supported
          width: parent.width
          textFormat: Text.PlainText
          wrapMode: Text.WordWrap
          text: "This plugin is written for the Lenovo ThinkPad Yoga 260 and will not "
            + "touch anything else. " + (root.blockedBy || "")
          color: root.bar.foreground
          opacity: 0.8
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.bodySmall
        }

        Text {
          visible: root.supported && root.messages && root.messages.length > 0
          width: parent.width
          textFormat: Text.PlainText
          wrapMode: Text.WordWrap
          text: root.messages.length > 0 ? root.messages[0] : ""
          color: root.bar.foreground
          opacity: 0.8
          font.family: root.bar.fontFamily
          font.pixelSize: Style.font.bodySmall
        }

        // ---------- Keyboard ----------
        // First, and the width of the panel: it is what a folded machine is
        // reached for most, and it has to be findable without reading.
        Button {
          visible: root.osk.installed.length > 0
          width: parent.width
          iconText: "󰌌" // nf-md-keyboard
          iconSize: Style.font.iconLarge
          text: "On-screen keyboard"
          fontSize: Style.font.body
          foreground: root.bar.foreground
          fontFamily: root.bar.fontFamily
          verticalPadding: Style.space(16)
          bordered: true
          hasCursor: root.cursorOn("keyboard")
          onClicked: { if (root.service) root.service.toggleKeyboard() }
          onHovered: function(h) { if (h) root.point("keyboard") }
        }

        // ---------- Screen ----------
        Row {
          id: screenRow
          width: parent.width
          spacing: Style.space(6)

          Repeater {
            model: root.screenActions
            Button {
              required property var modelData
              required property int index
              width: (screenRow.width - screenRow.spacing * 2) / 3
              iconText: modelData.icon
              iconSize: Style.font.iconLarge
              text: modelData.label
              fontSize: Style.font.bodySmall
              foreground: root.bar.foreground
              fontFamily: root.bar.fontFamily
              verticalPadding: Style.space(12)
              bordered: true
              active: index === 1 && root.locked
              hasCursor: root.cursorOn("screen", index)
              onClicked: modelData.run()
              onHovered: function(h) { if (h) root.point("screen", index) }
            }
          }
        }

        // ---------- Folded behaviour ----------
        Row {
          id: deviceRow
          width: parent.width
          spacing: Style.space(6)

          Repeater {
            model: root.deviceActions
            Button {
              required property var modelData
              required property int index
              width: (deviceRow.width - deviceRow.spacing) / 2
              iconText: modelData.icon
              iconSize: Style.font.icon
              text: modelData.label
              fontSize: Style.font.caption
              foreground: root.bar.foreground
              fontFamily: root.bar.fontFamily
              verticalPadding: Style.space(12)
              bordered: true
              active: index === 0 ? root.lockKeyboard : root.oskAuto
              hasCursor: root.cursorOn("devices", index)
              onClicked: modelData.run()
              onHovered: function(h) { if (h) root.point("devices", index) }
            }
          }
        }

        // ---------- Settings ----------
        // Set once, if ever. Folded away until asked for.
        Button {
          width: parent.width
          iconText: root.settingsShown ? "󰅗" : "󰅖" // nf-md-chevron_up / _down
          text: "Settings"
          fontSize: Style.font.bodySmall
          foreground: root.bar.foreground
          fontFamily: root.bar.fontFamily
          verticalPadding: Style.space(10)
          hasCursor: root.cursorOn("settings")
          onClicked: root.settingsShown = !root.settingsShown
          onHovered: function(h) { if (h) root.point("settings") }
        }

        Column {
          visible: root.settingsShown
          width: parent.width
          spacing: Style.space(10)

          PanelSectionHeader {
            text: "SCREEN TURNS THE WRONG WAY?"
            foreground: root.bar.foreground
            fontFamily: root.bar.fontFamily
          }

          ChoiceRow {
            rowId: "mapping"
            options: root.mappingOptions
            value: root.mappingOptions.some(function(o) { return o.value === root.mapping })
              ? root.mapping : "standard"
            onChosen: function(v) {
              root.save("mapping", v)
              if (root.service) root.service.setMapping(v)
            }
          }

          Text {
            width: parent.width
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            text: "Standard is right for this model. Pick the pair that comes out "
              + "mirrored. If the whole screen is upside down, run "
              + "`omarchy-yoga260-fold calibrate` to check the accelerometer mounting."
            color: root.bar.foreground
            opacity: 0.6
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
          }

          Text {
            width: parent.width
            textFormat: Text.PlainText
            wrapMode: Text.WordWrap
            text: "Hinge " + (root.tilt && root.tilt.residual !== undefined
              ? root.tilt.residual + "° between the two sensors" : "unknown")
              + (root.osk.drivable ? "" : " · no on-screen keyboard found")
            color: root.bar.foreground
            opacity: 0.5
            font.family: root.bar.fontFamily
            font.pixelSize: Style.font.caption
          }
        }
      }
    }
  }

  // One of N, as a row of equal buttons wide enough for a finger.
  component ChoiceRow: Row {
    id: choice
    property string rowId: ""
    property var options: []
    property string value: ""
    signal chosen(string value)

    width: parent.width
    spacing: Style.space(6)

    Repeater {
      model: choice.options
      Button {
        required property var modelData
        required property int index
        width: (choice.width - choice.spacing * (choice.options.length - 1)) / choice.options.length
        text: modelData.label
        fontSize: Style.font.caption
        foreground: root.bar.foreground
        fontFamily: root.bar.fontFamily
        verticalPadding: Style.space(10)
        bordered: true
        active: choice.value === modelData.value
        hasCursor: root.cursorOn(choice.rowId, index)
        onClicked: choice.chosen(modelData.value)
        onHovered: function(h) { if (h) root.point(choice.rowId, index) }
      }
    }
  }
}
