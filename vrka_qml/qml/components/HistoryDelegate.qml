pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import ".."
import "."

Rectangle {
    id: root

    required property string entryId
    required property string title
    required property string url
    required property string path
    required property string mode
    required property string timestamp

    implicitHeight: 74
    height: implicitHeight

    radius: Theme.cardRadius
    color: Theme.card
    border.width: 1
    border.color: Theme.border

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 14
        anchors.rightMargin: 14
        spacing: 12

        // Mode Icon Pill
        Rectangle {
            Layout.preferredWidth: 36
            Layout.preferredHeight: 36
            radius: 18
            color: Theme.cardAlt
            border.width: 1
            border.color: Theme.border

            Image {
                anchors.centerIn: parent
                width: 15
                height: 15
                source: {
                    if (root.mode === "audio") return Qt.resolvedUrl("../../../assets/branding/v2icons/music-accent-32.png")
                    return Qt.resolvedUrl("../../../assets/branding/v2icons/play-accent-32.png")
                }
                fillMode: Image.PreserveAspectFit
                mipmap: true
            }
        }

        // Center Details (Bounded width)
        ColumnLayout {
            Layout.fillWidth: true
            Layout.preferredWidth: 0
            spacing: 3

            Label {
                Layout.fillWidth: true
                Layout.preferredWidth: 0
                text: root.title || root.url || "Untitled transfer"
                font.family: Theme.fontFamily
                font.pixelSize: Theme.bodySize
                font.bold: true
                color: Theme.text
                elide: Text.ElideRight
                maximumLineCount: 1
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.preferredWidth: 0
                spacing: 8

                Label {
                    text: (root.mode === "audio" ? "AUDIO" : "VIDEO") + " \u2022 " + root.timestamp
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.microSize
                    font.bold: true
                    color: Theme.textMuted
                }

                Label {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 0
                    text: root.path || "No saved path"
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.microSize
                    color: Theme.textDim
                    elide: Text.ElideMiddle
                    maximumLineCount: 1
                }
            }
        }

        // Action Buttons
        RowLayout {
            spacing: 6

            VSecondaryButton {
                visible: root.path !== ""
                text: "Open"
                Layout.preferredHeight: 30
                onClicked: QueueController.openFolder(root.path)
            }

            VSecondaryButton {
                text: "Again"
                Layout.preferredHeight: 30
                onClicked: QueueController.redownload(root.url)
            }

            VSecondaryButton {
                text: "Remove"
                Layout.preferredHeight: 30
                onClicked: QueueController.removeHistoryEntry(root.entryId)
            }
        }
    }
}
