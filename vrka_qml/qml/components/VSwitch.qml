pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import ".."

Switch {
    id: root

    implicitWidth: 44
    implicitHeight: 24

    indicator: Rectangle {
        implicitWidth: 44
        implicitHeight: 24
        radius: 12
        color: root.checked ? Theme.accent : Theme.surfaceElevated
        border.width: root.visualFocus ? 2 : 1
        border.color: root.visualFocus ? Theme.focusRing : Theme.borderStrong

        Rectangle {
            x: root.checked ? parent.width - width - 3 : 3
            anchors.verticalCenter: parent.verticalCenter
            width: 18
            height: 18
            radius: 9
            color: Theme.textOnAccent

            Behavior on x { NumberAnimation { duration: 150; easing.type: Easing.OutCubic } }
        }
    }
}
