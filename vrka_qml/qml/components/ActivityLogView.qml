pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import ".."
import "."

Rectangle {
    id: root

    property bool collapsed: false
    property int logCount: Bridge.logLineCount

    implicitHeight: collapsed ? 42 : 180
    radius: Theme.cardRadius
    color: Theme.card
    border.width: 1
    border.color: Theme.border
    clip: true

    Behavior on implicitHeight { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 12
        spacing: 8

        // Header Bar
        RowLayout {
            Layout.fillWidth: true
            spacing: 8

            Item {
                Layout.preferredWidth: 44
                Layout.preferredHeight: 14
                Image {
                    anchors.centerIn: parent
                    source: Qt.resolvedUrl("../../../assets/branding/v2icons/terminal-accent-32.png")
                    width: 14
                    height: 14
                    fillMode: Image.PreserveAspectFit
                    mipmap: true
                }
            }

            Label {
                text: "ACTIVITY LOG"
                font.family: Theme.fontFamily
                font.pixelSize: Theme.smallSize
                font.bold: true
                color: Theme.text
            }

            Label {
                text: "(" + (Bridge.logLineCount > 0 ? (String(Bridge.logLineCount).padStart(3, "0") + " lines") : "empty") + ")"
                font.family: Theme.fontFamily
                font.pixelSize: Theme.microSize
                color: Theme.textDim
            }

            Item { Layout.fillWidth: true }

            VSecondaryButton {
                text: "Clear"
                Layout.preferredHeight: 26
                enabled: Bridge.logLineCount > 0
                onClicked: Bridge.clearLog()
            }

            VSecondaryButton {
                text: root.collapsed ? "Expand" : "Collapse"
                Layout.preferredHeight: 26
                onClicked: root.collapsed = !root.collapsed
            }
        }

        // Console Output Area (visible when not collapsed)
        Rectangle {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: !root.collapsed
            radius: Theme.controlRadius
            color: Theme.cardAlt
            border.width: 1
            border.color: Theme.border
            clip: true

            Flickable {
                id: logFlickable
                anchors.fill: parent
                anchors.margins: 8
                clip: true
                contentWidth: width
                contentHeight: logTextEdit.contentHeight
                boundsBehavior: Flickable.StopAtBounds

                ScrollBar.vertical: ScrollBar {
                    id: logVBar
                    active: true
                    policy: logFlickable.contentHeight > logFlickable.height ? ScrollBar.AlwaysOn : ScrollBar.AsNeeded
                }

                TextEdit {
                    id: logTextEdit
                    width: logFlickable.width - (logVBar.visible ? 14 : 0)
                    text: Bridge.logPlainText
                    font.family: Theme.fontFamily
                    font.pixelSize: 10
                    color: Theme.textMuted
                    selectedTextColor: Theme.text
                    selectionColor: Theme.accent
                    wrapMode: TextEdit.WrapAnywhere
                    readOnly: true
                    selectByMouse: true
                    selectByKeyboard: true
                    activeFocusOnPress: true

                    onTextChanged: {
                        Qt.callLater(function() {
                            logFlickable.contentY = Math.max(0, logFlickable.contentHeight - logFlickable.height);
                        });
                    }
                }
            }

            Label {
                anchors.centerIn: parent
                visible: Bridge.logLineCount === 0
                text: "No activity recorded yet"
                font.family: Theme.fontFamily
                font.pixelSize: Theme.smallSize
                color: Theme.textDisabled
            }
        }
    }
}
