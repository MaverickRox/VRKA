pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import ".."

Rectangle {
    id: root

    property string title: ""
    property string subtitle: ""
    property string iconName: ""
    property string iconText: ""
    property string headerIcon: ""
    property alias headerTitle: root.title
    property alias headerSubtitle: root.subtitle
    default property alias content: contentLayout.data

    Layout.preferredWidth: 0
    implicitWidth: contentLayout.implicitWidth + 2 * Theme.cardPadX
    implicitHeight: contentLayout.implicitHeight + 2 * Theme.cardPadX

    radius: Theme.cardRadius
    color: Theme.card
    border.width: 1
    border.color: Theme.border

    ColumnLayout {
        id: contentLayout

        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: Theme.cardPadX
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            Layout.preferredWidth: 0
            visible: root.title !== ""
            spacing: 10

            Image {
                visible: root.headerIcon !== "" || root.iconName !== ""
                source: root.headerIcon !== "" ? root.headerIcon
                      : root.iconName !== "" ? Qt.resolvedUrl("../../../assets/branding/v2icons/" + root.iconName + "-accent-32.png") : ""
                Layout.preferredWidth: 16
                Layout.preferredHeight: 16
                fillMode: Image.PreserveAspectFit
                mipmap: true
            }

            Text {
                visible: root.iconText !== "" && root.iconName === "" && root.headerIcon === ""
                text: root.iconText
                font.family: Theme.fontFamily
                font.pixelSize: Theme.sectionTitleSize
                color: Theme.accent
                verticalAlignment: Text.AlignVCenter
            }

            ColumnLayout {
                Layout.fillWidth: true
                Layout.preferredWidth: 0
                spacing: 2

                Label {
                    text: root.title
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.sectionTitleSize
                    font.bold: true
                    color: Theme.text
                }

                Label {
                    visible: root.subtitle !== ""
                    text: root.subtitle
                    font.family: Theme.fontFamily
                    font.pixelSize: Theme.microSize
                    color: Theme.textDim
                }
            }
        }
    }
}
