pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import "."
import "components"
import "pages"

ApplicationWindow {
    id: shell

    property int currentPageIndex: 0

    function setDarkMode(dark) {
        Theme.mode = dark ? "dark" : "light"
    }

    width: 1240
    height: 820
    minimumWidth: 1020
    minimumHeight: 700
    visible: true
    title: "VRKA - Media Downloader"
    color: Theme.bg

    // Left Navigation Sidebar (Material 1)
    Rectangle {
        id: sidebar

        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: Theme.sidebarWidth
        color: Theme.sidebar

        Rectangle {
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: Theme.hairline
            color: Theme.border
        }

        ColumnLayout {
            anchors.fill: parent
            spacing: 0

            // 1.5× Scaled Brand Header Lockup
            Item {
                Layout.fillWidth: true
                implicitHeight: 116

                RowLayout {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.leftMargin: 18
                    anchors.rightMargin: 18
                    anchors.topMargin: 20
                    spacing: 12

                    Image {
                        source: Qt.resolvedUrl("../../assets/branding/vrka-wolf-256.png")
                        Layout.preferredWidth: 72
                        Layout.preferredHeight: 72
                        fillMode: Image.PreserveAspectFit
                        mipmap: true
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 3

                        Label {
                            text: "VRKA"
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.brandTitleSize
                            font.bold: true
                            color: Theme.text
                        }

                        Label {
                            text: "MEDIA ENGINE"
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.microSize
                            font.bold: true
                            color: Theme.textDim
                        }
                    }
                }
            }

            // Hairline separator below branding
            Rectangle {
                Layout.fillWidth: true
                Layout.leftMargin: 18
                Layout.rightMargin: 18
                implicitHeight: Theme.hairline
                color: Theme.border
            }

            // Navigation Section
            ColumnLayout {
                Layout.fillWidth: true
                Layout.leftMargin: 12
                Layout.rightMargin: 12
                Layout.topMargin: 14
                spacing: 4

                Label {
                    text: "NAVIGATION"
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.microSize
                    font.bold: true
                    color: Theme.textDim
                    Layout.leftMargin: 8
                    Layout.bottomMargin: 4
                }

                VNavItem {
                    text: "Download"
                    iconName: "download"
                    selected: shell.currentPageIndex === 0
                    onClicked: shell.currentPageIndex = 0
                }

                VNavItem {
                    text: "Queue"
                    iconName: "list"
                    badgeCount: Bridge.activeCount
                    selected: shell.currentPageIndex === 1
                    onClicked: shell.currentPageIndex = 1
                }

                VNavItem {
                    text: "History"
                    iconName: "clock"
                    selected: shell.currentPageIndex === 2
                    onClicked: shell.currentPageIndex = 2
                }

                VNavItem {
                    text: "Settings"
                    iconName: "gear"
                    selected: shell.currentPageIndex === 3
                    onClicked: shell.currentPageIndex = 3
                }
            }

            Item { Layout.fillHeight: true }

            // Footer Telemetry Console (EXACTLY ONE UPLINK Console with EXACTLY ONE Status Light)
            Rectangle {
                Layout.fillWidth: true
                Layout.leftMargin: 12
                Layout.rightMargin: 12
                Layout.bottomMargin: 8
                implicitHeight: footerCol.implicitHeight + 18
                radius: Theme.controlRadius
                color: Theme.card
                border.width: 1
                border.color: Theme.border

                ColumnLayout {
                    id: footerCol
                    anchors.fill: parent
                    anchors.margins: 10
                    spacing: 8

                    // Engine Status Row (EXACTLY ONE GREEN STATUS LIGHT)
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8

                        Rectangle {
                            width: 8
                            height: 8
                            radius: 4
                            color: Bridge.activeCount > 0 ? Theme.accent : (Bridge.queuedCount > 0 ? Theme.warning : Theme.success)
                        }

                        Label {
                            text: Bridge.activeCount > 0 ? "UPLINK ACTIVE" : (Bridge.queuedCount > 0 ? "UPLINK QUEUED" : "UPLINK READY")
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.microSize
                            font.bold: true
                            color: Bridge.activeCount > 0 ? Theme.accent : (Bridge.queuedCount > 0 ? Theme.warning : Theme.success)
                        }

                        Item { Layout.fillWidth: true }

                        Label {
                            text: "v4.0.0"
                            font.family: Theme.fontFamily
                            font.pixelSize: Theme.microSize
                            font.bold: true
                            color: Theme.textDim
                        }
                    }

                    // Hairline divider
                    Rectangle {
                        Layout.fillWidth: true
                        implicitHeight: Theme.hairline
                        color: Theme.border
                    }

                    // 2x2 Telemetry Grid (Cohesive 3.0 layout without individual boxed cards)
                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        columnSpacing: 12
                        rowSpacing: 6

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            Label {
                                text: String(Bridge.queuedCount).padStart(2, "0")
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.smallSize
                                font.bold: true
                                color: Theme.text
                            }
                            Label {
                                text: "QUEUED"
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.microSize
                                font.bold: true
                                color: Theme.textDim
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            Label {
                                text: String(Bridge.activeCount).padStart(2, "0")
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.smallSize
                                font.bold: true
                                color: Bridge.activeCount > 0 ? Theme.accent : Theme.text
                            }
                            Label {
                                text: "ACTIVE"
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.microSize
                                font.bold: true
                                color: Bridge.activeCount > 0 ? Theme.accent : Theme.textDim
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            Label {
                                text: String(Bridge.historyCount).padStart(2, "0")
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.smallSize
                                font.bold: true
                                color: Theme.text
                            }
                            Label {
                                text: "ARCHIVED"
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.microSize
                                font.bold: true
                                color: Theme.textDim
                            }
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            Label {
                                text: String(Bridge.completedCount).padStart(2, "0")
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.smallSize
                                font.bold: true
                                color: Theme.success
                            }
                            Label {
                                text: "DONE"
                                font.family: Theme.fontFamily
                                font.pixelSize: Theme.microSize
                                font.bold: true
                                color: Theme.textDim
                            }
                        }
                    }
                }
            }

            // Single Dedicated Compact DAY MODE / NIGHT MODE Capsule Toggle (Reference Proportions)
            AbstractButton {
                id: themeToggle
                Layout.alignment: Qt.AlignHCenter
                Layout.preferredWidth: 118
                Layout.preferredHeight: 34
                Layout.bottomMargin: 14
                hoverEnabled: true
                onClicked: Theme.mode = Theme.isLight ? "dark" : "light"

                background: Rectangle {
                    id: pillBg
                    radius: parent.height / 2
                    color: Theme.isLight ? (themeToggle.down ? "#DADDE6" : themeToggle.hovered ? "#E4E7EE" : "#ECEEF4")
                                         : (themeToggle.down ? "#050508" : themeToggle.hovered ? "#141418" : "#000000")
                    border.width: 1
                    border.color: Theme.isLight ? (themeToggle.hovered ? "#A8ADC0" : "#C4C8D8")
                                                : (themeToggle.hovered ? "#444458" : "#282834")

                    // Subtle ambient purple tint on hover
                    Rectangle {
                        anchors.fill: parent
                        radius: parent.radius
                        color: Theme.accent
                        opacity: themeToggle.hovered ? 0.08 : 0.0
                    }
                }

                contentItem: Item {
                    anchors.fill: parent

                    // Prominent Circular End-Cap (30x30 White Disk per Reference)
                    Rectangle {
                        id: circleBadge
                        width: 30
                        height: 30
                        radius: 15
                        x: Theme.isLight ? 86 : 2
                        y: 2

                        color: "#FFFFFF"
                        border.width: 1
                        border.color: Theme.isLight ? "#C8CBD8" : "#38384C"

                        Image {
                            anchors.centerIn: parent
                            width: 16
                            height: 16
                            source: Theme.isLight ? Qt.resolvedUrl("../../assets/branding/v2icons/sun-lightMuted-32.png")
                                                  : Qt.resolvedUrl("../../assets/branding/v2icons/moon-lightMuted-32.png")
                            fillMode: Image.PreserveAspectFit
                            mipmap: true
                        }
                    }

                    // Bold State Theme Text Label (Deterministic Boundaries)
                    Text {
                        x: Theme.isLight ? 4 : 32
                        y: 0
                        width: 82
                        height: 34
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        text: Theme.isLight ? "LIGHT MODE" : "DARK MODE"
                        font.family: Theme.fontFamily
                        font.pixelSize: 10
                        font.bold: true
                        font.letterSpacing: 0.5
                        color: Theme.isLight ? "#111116" : "#FFFFFF"
                    }
                }
            }
        }
    }

    // Main Canvas Area
    Rectangle {
        id: mainCanvas
        anchors.left: sidebar.right
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        color: Theme.bg

        StackLayout {
            id: pageStack
            anchors.fill: parent
            anchors.margins: Theme.pagePadX
            currentIndex: shell.currentPageIndex

            DownloadPage { id: downloadView }
            QueuePage { id: queueView }
            HistoryPage { id: historyView }
            SettingsPage { id: settingsView }
        }
    }
}
