pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import ".."
import "../components"

Item {
    id: historyPage

    property bool hasEntries: Bridge.historyCount > 0

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
                    text: "History"
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.displayTitleSize
                    font.bold: true
                    color: Theme.text
                }

                Label {
                    Layout.fillWidth: true
                    Layout.preferredWidth: 0
                    text: "Access previously downloaded media, re-queue transfers, or reveal files."
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
                    text: String(Bridge.historyCount).padStart(2, "0") + " ARCHIVED ITEMS"
                    textColor: Theme.textDim
                }

                VSecondaryButton {
                    text: "Clear Archive"
                    Layout.preferredHeight: 34
                    enabled: Bridge.historyCount > 0
                    onClicked: Controller.clearHistory()
                }
            }
        }

        // Search and Filter Toolbar
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 42
            radius: Theme.controlRadius
            color: Theme.card
            border.width: 1
            border.color: Theme.border

            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                spacing: 10

                Image {
                    source: Qt.resolvedUrl("../../../assets/branding/v2icons/clock-accent-32.png")
                    Layout.preferredWidth: 14
                    Layout.preferredHeight: 14
                    fillMode: Image.PreserveAspectFit
                    mipmap: true
                }

                TextInput {
                    id: searchInput
                    Layout.fillWidth: true
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.smallSize
                    color: Theme.text
                    selectByMouse: true
                    clip: true
                    onTextChanged: Bridge.historyFilter = text

                    Text {
                        anchors.fill: parent
                        text: "Search archive by title, url or file path..."
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.smallSize
                        color: Theme.textDisabled
                        visible: !searchInput.text && !searchInput.activeFocus
                        verticalAlignment: Text.AlignVCenter
                    }
                }

                AbstractButton {
                    visible: searchInput.text !== ""
                    Layout.preferredWidth: 20
                    Layout.preferredHeight: 20
                    contentItem: Text {
                        text: "×"
                        font.family: Theme.fontFamily
                        font.pixelSize: 16
                        color: Theme.textMuted
                        anchors.centerIn: parent
                    }
                    onClicked: searchInput.text = ""
                }
            }
        }

        // Content Area (Archive List or Compact Empty State)
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            // Compact Empty State
            Rectangle {
                anchors.centerIn: parent
                visible: !historyPage.hasEntries
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
                        text: "Archive is empty"
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.sectionTitleSize
                        font.bold: true
                        color: Theme.text
                    }

                    Label {
                        Layout.alignment: Qt.AlignHCenter
                        text: "Completed downloads automatically appear here."
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

            // Archive Item List
            ListView {
                id: historyList
                anchors.fill: parent
                visible: historyPage.hasEntries
                clip: true
                spacing: 8
                model: Bridge.historyFiltered

                delegate: HistoryDelegate {
                    width: historyList.width
                }
            }
        }
    }
}
