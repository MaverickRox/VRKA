pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import ".."

Button {
    id: root

    property string iconName: ""

    implicitWidth: contentRow.implicitWidth + 24
    implicitHeight: Theme.controlHeight
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    font.family: Theme.fontFamily
    font.pixelSize: Theme.smallSize
    font.bold: true

    background: Rectangle {
        radius: Theme.controlRadius
        color: !root.enabled ? Theme.surfaceElevated
             : root.down ? Theme.surfaceHover
             : root.hovered ? Theme.surfaceElevated
             : Theme.cardAlt
        border.width: 1
        border.color: root.visualFocus ? Theme.focusRing : Theme.border
        opacity: root.enabled ? 1.0 : 0.4
    }

    contentItem: Row {
        id: contentRow
        spacing: 6
        anchors.centerIn: parent

        Image {
            visible: root.iconName !== ""
            source: root.iconName !== "" ? Qt.resolvedUrl("../../../assets/branding/v2icons/" + root.iconName + "-" + (Theme.isLight ? "lightMuted" : "muted") + "-32.png") : ""
            width: 14
            height: 14
            anchors.verticalCenter: parent.verticalCenter
            fillMode: Image.PreserveAspectFit
            mipmap: true
        }

        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: root.text
            font: root.font
            color: root.enabled ? Theme.text : Theme.textDisabled
        }
    }
}
