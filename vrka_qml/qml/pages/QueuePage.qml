pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import ".."
import "../components"

Item {
    id: queuePage

    property bool hasTasks: Bridge.taskCount > 0

    ColumnLayout {
        anchors.fill: parent
        spacing: 16

        // Header and Action Toolbar (Cohesive grid alignment)
        RowLayout {
            Layout.fillWidth: true
            spacing: 12

            ColumnLayout {
                Layout.fillWidth: true
                Layout.preferredWidth: 0
                spacing: 2

                Label {
                    text: "Download Queue"
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.displayTitleSize
                    font.bold: true
                    color: Theme.text
                }

                Label {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 0
                    text: "Manage active transfers and queue execution state."
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.bodySize
                    color: Theme.textDim
                    elide: Text.ElideRight
                }
            }

            RowLayout {
                spacing: 10
                Layout.alignment: Qt.AlignVCenter

                VPill {
                    text: String(Bridge.taskCount).padStart(2, "0") + " TASKS"
                    textColor: Theme.textDim
                }

                VSecondaryButton {
                    text: "Clear Completed"
                    Layout.preferredHeight: 34
                    enabled: Bridge.taskCount > 0
                    onClicked: QueueController.clearCompleted()
                }
            }
        }

        // Main Content Area (Task List or Compact Empty State)
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            // Compact Empty State
            Rectangle {
                anchors.centerIn: parent
                visible: !queuePage.hasTasks
                width: Math.min(parent.width - 40, 480)
                implicitHeight: emptyCol.implicitHeight + 36
                radius: Theme.cardRadius
                color: Theme.card
                border.width: 1
                border.color: Theme.border

                ColumnLayout {
                    id: emptyCol
                    anchors.centerIn: parent
                    spacing: 12
                    width: parent.width - 40

                    Image {
                        Layout.alignment: Qt.AlignHCenter
                        source: Qt.resolvedUrl("../../../assets/branding/vrka-wolf-256.png")
                        Layout.preferredWidth: 42
                        Layout.preferredHeight: 42
                        fillMode: Image.PreserveAspectFit
                        mipmap: true
                        opacity: 0.7
                    }

                    Label {
                        Layout.alignment: Qt.AlignHCenter
                        text: "Queue is empty"
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.sectionTitleSize
                        font.bold: true
                        color: Theme.text
                    }

                    Label {
                        Layout.alignment: Qt.AlignHCenter
                        text: "Paste a media link in Download to begin downloading."
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.smallSize
                        color: Theme.textDim
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.WordWrap
                        Layout.fillWidth: true
                    }

                    Item { Layout.preferredHeight: 4 }

                    VPrimaryButton {
                        Layout.alignment: Qt.AlignHCenter
                        text: "Go to Download"
                        Layout.preferredHeight: 34
                        Layout.preferredWidth: 160
                        onClicked: shell.currentPageIndex = 0
                    }
                }
            }

            // Active Task List
            ListView {
                id: queueList
                anchors.fill: parent
                visible: queuePage.hasTasks
                clip: true
                spacing: 10
                model: Bridge.tasksModel

                delegate: TaskDelegate {
                    width: queueList.width
                }
            }
        }

        // Secondary Activity Log
        ActivityLogView {
            Layout.fillWidth: true
        }
    }
}
