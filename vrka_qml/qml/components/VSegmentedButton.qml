pragma ComponentBehavior: Bound
import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import ".."

Rectangle {
    id: root

    property int selectedIndex: 0
    property var options: ["Video", "Audio Only"]
    signal optionSelected(int index)

    implicitWidth: 260
    implicitHeight: Theme.controlHeight
    radius: Theme.controlRadius
    color: Theme.cardAlt
    border.width: 1
    border.color: Theme.border

    RowLayout {
        anchors.fill: parent
        anchors.margins: 3
        spacing: 4

        Repeater {
            model: root.options

            delegate: AbstractButton {
                id: segBtn
                required property int index
                required property string modelData

                Layout.fillWidth: true
                Layout.fillHeight: true
                hoverEnabled: true

                background: Rectangle {
                    radius: Theme.controlRadius - 2
                    color: root.selectedIndex === segBtn.index ? Theme.accent
                         : segBtn.hovered ? Theme.surfaceElevated
                         : "transparent"
                }

                contentItem: RowLayout {
                    spacing: 6
                    anchors.centerIn: parent

                    Image {
                        Layout.preferredWidth: 14
                        Layout.preferredHeight: 14
                        source: {
                            var icon = segBtn.index === 0 ? "play" : "music"
                            var variant = root.selectedIndex === segBtn.index ? "white" : (Theme.isLight ? "lightMuted" : "muted")
                            if (root.selectedIndex === segBtn.index) return Qt.resolvedUrl("../../../assets/branding/v2icons/" + icon + "-white-32.png")
                            return Qt.resolvedUrl("../../../assets/branding/v2icons/" + icon + "-" + variant + "-32.png")
                        }
                        fillMode: Image.PreserveAspectFit
                        mipmap: true
                    }

                    Text {
                        text: segBtn.modelData
                        font.family: Theme.fontFamily
                        font.pixelSize: Theme.smallSize
                        font.bold: root.selectedIndex === segBtn.index
                        color: root.selectedIndex === segBtn.index ? Theme.textOnAccent : Theme.textMuted
                        verticalAlignment: Text.AlignVCenter
                        horizontalAlignment: Text.AlignHCenter
                    }
                }

                onClicked: {
                    root.selectedIndex = segBtn.index
                    root.optionSelected(segBtn.index)
                }
            }
        }
    }
}
