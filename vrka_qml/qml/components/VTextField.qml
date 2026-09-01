pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import ".."

TextField {
    id: root

    implicitWidth: 120
    implicitHeight: Theme.controlHeight
    selectByMouse: true
    font.family: Theme.fontFamily
    font.pixelSize: Theme.bodySize
    color: Theme.text
    placeholderTextColor: Theme.textDisabled
    selectionColor: Theme.accent
    selectedTextColor: Theme.textOnAccent
    verticalAlignment: TextInput.AlignVCenter
    leftPadding: 12
    rightPadding: 12

    background: Rectangle {
        radius: Theme.controlRadius
        color: root.readOnly ? Theme.surfaceElevated : Theme.cardAlt
        border.width: root.activeFocus ? 1.5 : 1
        border.color: root.activeFocus ? Theme.focusRing : Theme.borderStrong
    }
}
