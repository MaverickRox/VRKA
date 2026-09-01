pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import ".."

Button {
    id: root

    property string iconName: ""

    implicitWidth: contentRow.implicitWidth + 32
    implicitHeight: Theme.primaryButtonHeight
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    font.family: Theme.fontFamily
    font.pixelSize: Theme.bodySize
    font.bold: true

    background: Rectangle {
        radius: Theme.controlRadius
        color: !root.enabled ? Theme.surfaceElevated
             : root.down ? Theme.accentPressed
             : root.hovered ? Theme.accentHover
             : Theme.accent
        opacity: root.enabled ? 1.0 : 0.4
        border.width: root.visualFocus ? 1.5 : 0
        border.color: Theme.focusRing
    }

    contentItem: Row {
        id: contentRow
        spacing: 8
        anchors.centerIn: parent

        Image {
            visible: root.iconName !== ""
            source: root.iconName !== "" ? Qt.resolvedUrl("../../../assets/branding/v2icons/" + root.iconName + "-accent-32.png") : ""
            width: 16
            height: 16
            anchors.verticalCenter: parent.verticalCenter
            fillMode: Image.PreserveAspectFit
            mipmap: true
        }

        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: root.text
            font: root.font
            color: root.enabled ? Theme.textOnAccent : Theme.textDisabled
        }
    }
}
