pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import ".."

ComboBox {
    id: root

    implicitWidth: 140
    implicitHeight: Theme.controlHeight
    font.family: Theme.fontFamily
    font.pixelSize: Theme.bodySize

    background: Rectangle {
        radius: Theme.controlRadius
        color: Theme.cardAlt
        border.width: root.visualFocus ? 1.5 : 1
        border.color: root.visualFocus ? Theme.focusRing : Theme.borderStrong
    }

    contentItem: Text {
        leftPadding: 12
        rightPadding: root.indicator.width + 12
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
        text: root.displayText
        font: root.font
        color: Theme.text
    }

    indicator: Text {
        anchors.right: parent.right
        anchors.rightMargin: 12
        anchors.verticalCenter: parent.verticalCenter
        text: "\u25BE"
        font.pixelSize: Theme.sectionTitleSize
        color: Theme.textMuted
    }

    delegate: ItemDelegate {
        id: itemDelegate

        required property var model
        required property int index

        width: root.width
        height: Theme.controlHeight
        highlighted: root.highlightedIndex === index
        font.family: Theme.fontFamily
        font.pixelSize: Theme.bodySize

        background: Rectangle {
            radius: Theme.controlRadius - 1
            color: itemDelegate.highlighted ? Theme.accentSoft : "transparent"
        }

        contentItem: Text {
            leftPadding: 12
            verticalAlignment: Text.AlignVCenter
            text: itemDelegate.model[root.textRole] !== undefined ? itemDelegate.model[root.textRole] : itemDelegate.model
            font: itemDelegate.font
            color: itemDelegate.highlighted ? Theme.text : Theme.textMuted
            elide: Text.ElideRight
        }
    }

    popup: Popup {
        y: root.height + 4
        width: root.width
        padding: 4
        implicitHeight: Math.min(240, contentItem.implicitHeight + 8)

        background: Rectangle {
            radius: Theme.controlRadius
            color: Theme.card
            border.width: 1
            border.color: Theme.borderStrong
        }

        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: root.popup.visible ? root.delegateModel : null
            currentIndex: root.highlightedIndex
            boundsBehavior: Flickable.StopAtBounds
        }
    }
}
