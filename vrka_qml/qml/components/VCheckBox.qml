pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import ".."

CheckBox {
    id: root

    Layout.preferredWidth: 0
    implicitWidth: contentText.implicitWidth + indicator.width + 14
    implicitHeight: Math.max(26, contentText.implicitHeight + 6)
    font.family: Theme.fontFamily
    font.pixelSize: Theme.bodySize

    indicator: Rectangle {
        x: 0
        y: Math.max(0, Math.floor((root.font.pixelSize - 18) / 2) + 2)
        width: 18
        height: 18
        radius: 4
        color: root.checked ? Theme.accent : Theme.cardAlt
        border.width: root.visualFocus ? 2 : 1
        border.color: root.visualFocus ? Theme.focusRing
                    : root.checked ? Theme.accent : Theme.borderStrong

        Text {
            anchors.centerIn: parent
            visible: root.checked
            text: "\u2713"
            font.pixelSize: Theme.smallSize
            font.bold: true
            color: Theme.textOnAccent
        }
    }

    contentItem: Text {
        id: contentText
        leftPadding: root.indicator.width + 10
        width: root.width > 0 ? root.width - (root.indicator.width + 10) : undefined
        verticalAlignment: Text.AlignVCenter
        text: root.text
        font: root.font
        color: root.enabled ? Theme.text : Theme.textDisabled
        wrapMode: Text.WordWrap
    }
}
